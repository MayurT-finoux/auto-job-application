"""Load and validate configuration.

Sources, in order of precedence:
  * environment variables  - secrets (Gmail, database, API keys)
  * the database           - job profile and sending settings edited from the dashboard
  * job_profile.yaml       - default job profile (copied into the database on first run)
  * config.yaml            - defaults for everything else
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import string
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

if TYPE_CHECKING:
    from .store import Store


class ConfigError(Exception):
    pass


WORK_MODES = ("onsite", "hybrid", "remote")


@dataclass
class Config:
    # --- job profile ---
    job_description: str
    role_titles: list[str]
    keywords: list[str]
    exclude_keywords: list[str]
    exp_min: float
    exp_max: float
    locations: list[str]
    work_modes: list[str]
    min_score: float
    # --- sending settings (editable in the dashboard) ---
    interval_minutes: float
    daily_limit: int
    active_hours: dict | None
    test_recipient: str
    # --- fixed settings (config.yaml) ---
    timezone: str
    resume_path: Path
    max_queries_per_round: int
    sources: dict
    applicant: dict
    email_subject: str
    email_body: str
    gmail_user: str = ""
    gmail_app_password: str = ""
    env: dict = field(default_factory=dict)
    resume_name: str = ""
    resume_bytes: bytes = b""

    @property
    def remote_ok(self) -> bool:
        return "remote" in self.work_modes

    @property
    def search_locations(self) -> list[str]:
        locs = list(self.locations)
        if self.remote_ok and not any(l.lower() == "remote" for l in locs):
            locs.append("Remote")
        return locs or [""]

    def now(self) -> datetime:
        return datetime.now(ZoneInfo(self.timezone)).replace(tzinfo=None)

    def now_str(self) -> str:
        return self.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean_list(values) -> list[str]:
    if isinstance(values, str):
        values = values.replace("\n", ",").split(",")
    return [str(v).strip() for v in values or [] if str(v).strip()]


# ------------------------------------------------------------------ job profile
def validate_profile(raw: dict) -> dict:
    """Normalise a job profile dict; raise ConfigError if invalid."""
    roles = _clean_list(raw.get("role_titles"))
    if not roles:
        raise ConfigError("Add at least one job title")
    exp = raw.get("experience") or {}
    try:
        exp_min, exp_max = float(exp.get("min", 0)), float(exp.get("max", 50))
        min_score = float(raw.get("min_score", 0.4))
    except (TypeError, ValueError):
        raise ConfigError("Experience and match strictness must be numbers")
    if exp_min < 0 or exp_min > exp_max:
        raise ConfigError("Experience min must be between 0 and max")
    if not 0 <= min_score <= 1:
        raise ConfigError("Match strictness must be between 0 and 1")
    modes = [m.lower() for m in _clean_list(raw.get("work_modes")) if m.lower() in WORK_MODES]
    if not modes:
        raise ConfigError("Select at least one work mode")
    locations = _clean_list(raw.get("locations"))
    if not locations and modes != ["remote"]:
        raise ConfigError("Add at least one location (or choose remote only)")
    num = lambda v: int(v) if float(v).is_integer() else v
    return {
        "job_description": str(raw.get("job_description") or "").strip(),
        "role_titles": roles,
        "keywords": [k.lower() for k in _clean_list(raw.get("keywords"))],
        "exclude_keywords": [k.lower() for k in _clean_list(raw.get("exclude_keywords"))],
        "experience": {"min": num(exp_min), "max": num(exp_max)},
        "locations": locations,
        "work_modes": modes,
        "min_score": min_score,
    }


def profile_id(profile: dict) -> str:
    """Short fingerprint of a job profile; changes whenever any field of the profile changes."""
    blob = json.dumps(validate_profile(profile), sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def load_profile_file(path: Path) -> dict:
    if not Path(path).exists():
        raise ConfigError(f"Job profile not found: {path}")
    return validate_profile(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


def get_profile(store: "Store | None", default_path: Path) -> dict:
    """Profile from the database; seeded from job_profile.yaml the first time."""
    if store is not None:
        prof = store.get("profile")
        if prof:
            return validate_profile(prof)
    prof = load_profile_file(default_path)
    if store is not None:
        store.set("profile", prof)
    return prof


def save_profile(store: "Store", raw: dict) -> tuple[dict, bool]:
    """Save the profile. Returns (profile, changed)."""
    prof = validate_profile(raw)
    old = store.get("profile")
    if old:
        # openings found so far belong to the old profile
        store.adopt_untagged_openings(profile_id(old))
    store.set("profile", prof)
    return prof, (not old or profile_id(old) != profile_id(prof))


# ------------------------------------------------------------------ sending settings
def _hhmm(value: str, name: str) -> str:
    try:
        return datetime.strptime(str(value).strip(), "%H:%M").strftime("%H:%M")
    except ValueError:
        raise ConfigError(f"{name} must look like 09:00")


def validate_settings(raw: dict) -> dict:
    try:
        interval = float(raw.get("interval_minutes", 60))
        daily = int(float(raw.get("daily_limit", 10)))
    except (TypeError, ValueError):
        raise ConfigError("Interval and daily limit must be numbers")
    if interval < 15:
        raise ConfigError("Interval must be at least 15 minutes (Vercel runs the bot every 15 minutes)")
    if not 1 <= daily <= 200:
        raise ConfigError("Daily limit must be between 1 and 200")
    test = str(raw.get("test_recipient") or "").strip()
    if test and "@" not in test:
        raise ConfigError("Test recipient must be an email address")
    out = {"interval_minutes": int(interval) if interval.is_integer() else interval,
           "daily_limit": daily, "test_recipient": test,
           "active_hours_enabled": bool(raw.get("active_hours_enabled"))}
    if out["active_hours_enabled"]:
        out["active_start"] = _hhmm(raw.get("active_start", "09:00"), "Start time")
        out["active_end"] = _hhmm(raw.get("active_end", "18:00"), "End time")
    return out


# ------------------------------------------------------------------ email template
PLACEHOLDERS = ("company", "role", "name", "phone", "linkedin", "experience", "email")
APPLICANT_FIELDS = ("name", "phone", "linkedin", "experience")


def _check_placeholders(text: str, label: str) -> None:
    try:
        names = [f for _, f, _, _ in string.Formatter().parse(text) if f is not None]
    except ValueError:
        raise ConfigError(f"{label} has a {{ or }} without its pair. Placeholders look like {{company}}.")
    bad = sorted({n for n in names if n not in PLACEHOLDERS})
    if bad:
        shown = ", ".join("{" + b + "}" for b in bad)
        allowed = " ".join("{" + p + "}" for p in PLACEHOLDERS)
        raise ConfigError(f"{label} uses unknown placeholder {shown}. Allowed: {allowed}")


def validate_email_template(raw: dict) -> dict:
    subject = " ".join(str(raw.get("subject") or "").split())
    body = str(raw.get("body") or "").replace("\r\n", "\n").strip()
    if not subject:
        raise ConfigError("The subject can't be empty")
    if not body:
        raise ConfigError("The email body can't be empty")
    if len(subject) > 200 or len(body) > 5000:
        raise ConfigError("Subject must be under 200 characters and body under 5000")
    _check_placeholders(subject, "Subject")
    _check_placeholders(body, "Body")
    applicant = {k: str((raw.get("applicant") or {}).get(k) or "").strip() for k in APPLICANT_FIELDS}
    if not applicant["name"]:
        raise ConfigError("Your name can't be empty")
    return {"subject": subject, "body": body, "applicant": applicant}


def default_email_template(raw: dict) -> dict:
    return {
        "subject": raw.get("email_subject") or "Application for {role} - {name}",
        "body": (raw.get("email_body") or "").strip(),
        "applicant": {k: str((raw.get("applicant") or {}).get(k) or "") for k in APPLICANT_FIELDS},
    }


def get_email_template(store: "Store | None", raw: dict) -> dict:
    """Template edited in the dashboard, else the defaults from config.yaml."""
    saved = store.get("email_template") if store is not None else None
    return saved or default_email_template(raw)


def save_email_template(store: "Store", raw: dict) -> dict:
    tpl = validate_email_template(raw)
    store.set("email_template", tpl)
    return tpl


# ------------------------------------------------------------------ main loader
def load_env_files(base: Path) -> None:
    """Load .env.local, then .env. Never overrides variables already set (e.g. on Vercel)."""
    for name in (".env.local", ".env"):
        load_dotenv(Path(base) / name, override=False)


def read_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def default_settings(raw: dict) -> dict:
    ah = raw.get("active_hours") or {}
    return {
        "interval_minutes": raw.get("interval_minutes", 60),
        "daily_limit": raw.get("daily_limit", 10),
        "test_recipient": raw.get("test_recipient") or "",
        "active_hours_enabled": bool(ah),
        "active_start": str(ah.get("start", "09:00")),
        "active_end": str(ah.get("end", "18:00")),
    }


def get_settings(store: "Store | None", raw: dict) -> dict:
    settings = default_settings(raw)
    if store is not None:
        settings.update(store.get("settings") or {})
    return settings


def load_config(path: str | Path = "config.yaml", store: "Store | None" = None,
                require_mail: bool = True) -> Config:
    path = Path(path)
    base = path.parent
    load_env_files(base)
    raw = read_yaml(path)

    prof = get_profile(store, base / raw.get("profile_path", "job_profile.yaml"))
    s = get_settings(store, raw)
    tpl = get_email_template(store, raw)
    tz = raw.get("timezone", "Asia/Kolkata")

    resume_path = base / raw.get("resume_path", "resume/resume.pdf")
    resume_name, resume_bytes = "", b""
    stored = store.get("resume") if store is not None else None
    if stored and stored.get("b64"):
        resume_name, resume_bytes = stored.get("name", "resume.pdf"), base64.b64decode(stored["b64"])
    elif resume_path.exists():
        resume_name, resume_bytes = resume_path.name, resume_path.read_bytes()

    cfg = Config(
        job_description=prof["job_description"],
        role_titles=prof["role_titles"],
        keywords=prof["keywords"],
        exclude_keywords=prof["exclude_keywords"],
        exp_min=float(prof["experience"]["min"]),
        exp_max=float(prof["experience"]["max"]),
        locations=prof["locations"],
        work_modes=prof["work_modes"],
        min_score=prof["min_score"],
        interval_minutes=float(s["interval_minutes"]),
        daily_limit=int(s["daily_limit"]),
        active_hours=({"start": s["active_start"], "end": s["active_end"], "tz": tz}
                      if s.get("active_hours_enabled") else None),
        test_recipient=(s.get("test_recipient") or "").strip(),
        timezone=tz,
        resume_path=resume_path,
        max_queries_per_round=int(raw.get("max_queries_per_round", 8)),
        sources=raw.get("sources") or {"duckduckgo": True},
        applicant=tpl["applicant"],
        email_subject=tpl["subject"],
        email_body=tpl["body"],
        gmail_user=os.getenv("GMAIL_USER", "").strip(),
        gmail_app_password=os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "").strip(),
        env={k: os.getenv(k, "") for k in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "JOOBLE_API_KEY")},
        resume_name=resume_name,
        resume_bytes=resume_bytes,
    )

    if require_mail:
        problems = []
        if not cfg.resume_bytes:
            problems.append("no resume uploaded (upload it in the dashboard or put it at resume/resume.pdf)")
        if not cfg.gmail_user or not cfg.gmail_app_password:
            problems.append("GMAIL_USER / GMAIL_APP_PASSWORD environment variables are not set")
        if problems:
            raise ConfigError("; ".join(problems))
    return cfg
