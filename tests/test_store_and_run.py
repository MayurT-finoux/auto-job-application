import os
import shutil
from pathlib import Path

import pytest

from jobbot import scheduler
from jobbot.config import ConfigError, get_profile, load_config, save_profile, validate_settings
from jobbot.search.base import Lead
from jobbot.store import Store

ROOT = Path(__file__).resolve().parent.parent
PG_URL = os.getenv("TEST_DATABASE_URL", "")


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path):
    if request.param == "postgres":
        if not PG_URL:
            pytest.skip("set TEST_DATABASE_URL to run Postgres tests")
        s = Store(url=PG_URL)
        for t in ("applications", "openings", "kv", "bot_log"):
            s._exec(f"DELETE FROM {t}")
    else:
        s = Store(url="", sqlite_path=tmp_path / "t.db")
    yield s
    s.close()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A copy of the config files in a temp dir, with Gmail env vars cleared."""
    for f in ("config.yaml", "job_profile.yaml"):
        shutil.copy(ROOT / f, tmp_path / f)
    monkeypatch.setenv("GMAIL_USER", "")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "")
    return tmp_path


def test_kv_and_tables(store):
    store.set("x", {"a": [1, 2]})
    assert store.get("x") == {"a": [1, 2]}
    store.set("x", 5)
    assert store.get("x") == 5 and store.get("missing", "d") == "d"
    row = {"found_at": "2026-11-18 10:00:00", "company": "Acme", "title": "Dev", "url": "https://a.com/1"}
    assert store.add_opening(row) is True
    assert store.add_opening(row) is False  # same URL ignored
    assert store.mark_manual("https://a.com/1", "2026-11-18 11:00:00")
    assert store.openings()[0]["manual_at"] == "2026-11-18 11:00:00"
    store.add_application({"ts": "2026-11-18 10:00:00", "company": "Acme", "email": "hr@a.com", "status": "sent"})
    store.add_application({"ts": "2026-11-18 10:05:00", "company": "B", "email": "hr@b.com", "status": "failed"})
    assert store.sent_count_on("2026-11-18") == 1
    assert len(store.applications(status="sent")) == 1
    store.add_log_lines([f"line {i}" for i in range(600)])
    tail = store.log_tail(3)
    assert tail == ["line 597", "line 598", "line 599"]


def test_profile_seed_and_save(store, project):
    prof = get_profile(store, project / "job_profile.yaml")
    assert prof["role_titles"] and store.get("profile") == prof
    saved, _ = save_profile(store, {"role_titles": "Python Developer\nSRE", "keywords": "Python, AWS",
                                 "experience": {"min": "2", "max": "5"}, "locations": "Pune",
                                 "work_modes": ["remote", "bogus"], "min_score": "0.4"})
    assert saved["role_titles"] == ["Python Developer", "SRE"] and saved["work_modes"] == ["remote"]
    assert get_profile(store, project / "job_profile.yaml") == saved
    with pytest.raises(ConfigError):
        save_profile(store, {"role_titles": "Dev", "experience": {"min": 6, "max": 2}, "work_modes": ["remote"]})


def test_settings_validation():
    s = validate_settings({"interval_minutes": "30", "daily_limit": "5", "active_hours_enabled": True,
                           "active_start": "9:00", "active_end": "18:30"})
    assert s["interval_minutes"] == 30 and s["active_start"] == "09:00"
    with pytest.raises(ConfigError):
        validate_settings({"interval_minutes": "5"})


def test_settings_override_config(store, project):
    store.set("settings", validate_settings({"interval_minutes": 90, "daily_limit": 3}))
    cfg = load_config(project / "config.yaml", store, require_mail=False)
    assert cfg.interval_minutes == 90 and cfg.daily_limit == 3 and cfg.active_hours is None
    with pytest.raises(ConfigError):
        load_config(project / "config.yaml", store, require_mail=True)  # no Gmail env / resume


def test_run_once_dry_run(store, project, monkeypatch):
    leads = [
        Lead(company="Acme", title="Python Developer", url="https://acme.example/jobs/1",
             snippet="Python Django AWS, 2-4 years", source="test", emails=["hr@acme.example"]),
        Lead(company="Senior Co", title="Python Developer", url="https://senior.example/jobs/2",
             snippet="Python, 10+ years", source="test"),
    ]
    monkeypatch.setattr(scheduler, "gather_leads", lambda cfg: [Lead(**l.__dict__) for l in leads])
    monkeypatch.setattr(scheduler, "resolve_email", lambda lead: lead.emails[0] if lead.emails else None)
    cfg_path = project / "config.yaml"

    result = scheduler.run_once(store, cfg_path, dry_run=True, force=True)
    assert "would email hr@acme.example" in result
    assert [o["company"] for o in store.openings()] == ["Acme"]  # 10+ yrs posting filtered out
    assert store.applications()[0]["status"] == "dry-run"
    assert store.get("status")["last_result"] == result
    assert any("DRY RUN" in l for l in store.log_tail(50))

    store.set("paused", True)
    assert scheduler.run_once(store, cfg_path, dry_run=True) == "Paused from the dashboard"


def test_run_once_respects_interval(store, project, monkeypatch):
    import time
    monkeypatch.setattr(scheduler, "gather_leads", lambda cfg: [])
    store.set("last_send_epoch", time.time())
    result = scheduler.run_once(store, project / "config.yaml", dry_run=True)
    assert result.startswith("Not due yet")


def test_profile_change_starts_fresh_openings_list(store, project):
    from jobbot.config import profile_id
    base = {"role_titles": "Python Developer", "keywords": "python", "experience": {"min": 2, "max": 5},
            "locations": "Pune", "work_modes": ["remote"]}
    prof, changed = save_profile(store, base)
    assert changed
    old = profile_id(prof)
    row = lambda url, pid: {"found_at": "2026-11-18 10:00:00", "company": "A", "title": "Dev", "url": url,
                            "profile_id": pid}
    assert store.add_opening(row("https://a.com/1", old))
    assert store.add_opening(row("https://a.com/2", old))
    store.mark_manual("https://a.com/2", "2026-11-18 11:00:00")

    # saving the same profile again changes nothing
    _, changed = save_profile(store, base)
    assert not changed and len(store.openings(old)) == 2

    # a different profile: current list is empty, old openings are "previous"
    new_prof, changed = save_profile(store, {**base, "role_titles": "Data Engineer"})
    new = profile_id(new_prof)
    assert changed and new != old
    assert store.openings(new) == [] and store.count_previous_openings(new) == 2

    # an old link found again under the new profile moves to the current list, keeping its manual mark
    assert store.add_opening(row("https://a.com/2", new))
    assert not store.add_opening(row("https://a.com/2", new))
    current = store.openings(new)
    assert [o["url"] for o in current] == ["https://a.com/2"] and current[0]["manual_at"]
    assert [o["url"] for o in store.openings(new, previous=True)] == ["https://a.com/1"]


def test_untagged_openings_adopted(store):
    store.add_opening({"found_at": "x", "url": "https://legacy.com/1"})  # saved by an older version
    store.adopt_untagged_openings("abc")
    assert [o["url"] for o in store.openings("abc")] == ["https://legacy.com/1"]
