"""Dashboard: track applications and openings, edit the job profile and settings, control the bot.

Local:   python dashboard.py         -> http://127.0.0.1:5000
Vercel:  served by api/index.py; Vercel Schedules call /api/run every 15 minutes.

Environment variables:
  DASHBOARD_PASSWORD  login password (required on Vercel)
  CRON_SECRET         Vercel sends it to /api/run; the endpoint rejects calls without it
  DATABASE_URL        Neon/Postgres connection string (otherwise local SQLite)
  GMAIL_USER, GMAIL_APP_PASSWORD, and optional ADZUNA_*/JOOBLE_API_KEY
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import os
from collections import Counter
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import (Flask, Response, flash, g, jsonify, redirect, render_template, request, session,
                   url_for)

from jobbot.config import (WORK_MODES, ConfigError, get_profile, get_settings, load_env_files, profile_id,
                           read_yaml,
                           save_profile, validate_settings)
from jobbot.scheduler import run_once
from jobbot.store import APP_FIELDS, OPENING_FIELDS, Store, database_url

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.yaml"
load_env_files(BASE)

ON_VERCEL = bool(os.getenv("VERCEL"))
PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
CRON_SECRET = os.getenv("CRON_SECRET", "")
MAX_RESUME_BYTES = 3 * 1024 * 1024  # Vercel request bodies are limited to 4.5 MB

app = Flask(__name__, template_folder=str(BASE / "templates"))
app.secret_key = os.getenv("SECRET_KEY") or hashlib.sha256(f"jobbot:{PASSWORD}".encode()).hexdigest()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=ON_VERCEL, PERMANENT_SESSION_LIFETIME=timedelta(days=14),
                  MAX_CONTENT_LENGTH=MAX_RESUME_BYTES + 64 * 1024)


# ---------------------------------------------------------------- storage per request
def store() -> Store:
    if "store" not in g:
        g.store = Store(sqlite_path=BASE / "data" / "jobbot.db")
    return g.store


@app.teardown_appcontext
def _close_store(_exc):
    s = g.pop("store", None)
    if s is not None:
        s.close()


# ---------------------------------------------------------------- auth
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if ON_VERCEL and not PASSWORD:
            return render_template("login.html", setup_error=True), 503
        if PASSWORD and not session.get("ok"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    if ON_VERCEL and not PASSWORD:
        return render_template("login.html", setup_error=True), 503
    if request.method == "POST":
        if PASSWORD and hmac.compare_digest(request.form.get("password", ""), PASSWORD):
            session.permanent = True
            session["ok"] = True
            nxt = request.args.get("next", "/")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
        flash("Wrong password.", "error")
    return render_template("login.html", setup_error=False)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- scheduled run
@app.route("/api/run", methods=["GET", "POST"])
def api_run():
    """Called by Vercel Schedules (Authorization: Bearer <CRON_SECRET>)."""
    auth = request.headers.get("Authorization", "")
    cron_ok = bool(CRON_SECRET) and hmac.compare_digest(auth, f"Bearer {CRON_SECRET}")
    if not cron_ok and not (session.get("ok") or (not PASSWORD and not ON_VERCEL)):
        return jsonify(error="unauthorized"), 401
    result = run_once(store(), CONFIG_PATH)
    return jsonify(result=result)


# ---------------------------------------------------------------- helpers
def safe_url(url: str) -> str:
    return url if (url or "").startswith(("http://", "https://")) else ""


def daily_series(apps: list[dict], today, days: int = 14) -> list[dict]:
    counts = Counter((r["ts"] or "")[:10] for r in apps if r.get("status") == "sent")
    out = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        out.append({"date": d.isoformat(), "label": d.strftime("%d %b"), "count": counts.get(d.isoformat(), 0)})
    return out


def local_now_str() -> str:
    tz = ZoneInfo(read_yaml(CONFIG_PATH).get("timezone", "Asia/Kolkata"))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M")


LOCAL_RESUME = BASE / "resume" / "resume.pdf"


def current_resume(s: Store) -> tuple[bytes, str]:
    """The resume the bot will attach: the uploaded one, else resume/resume.pdf."""
    stored = s.get("resume") or {}
    if stored.get("b64"):
        return base64.b64decode(stored["b64"]), stored.get("name", "resume.pdf")
    if LOCAL_RESUME.exists():
        return LOCAL_RESUME.read_bytes(), LOCAL_RESUME.name
    return b"", ""


def resume_info(s: Store) -> dict:
    stored = s.get("resume") or {}
    if stored.get("b64"):
        size = stored.get("size") or len(base64.b64decode(stored["b64"]))
        return {"name": stored.get("name", "resume.pdf"), "kb": max(1, size // 1024),
                "uploaded_at": stored.get("uploaded_at", ""), "source": "uploaded"}
    if LOCAL_RESUME.exists():
        return {"name": LOCAL_RESUME.name, "kb": max(1, LOCAL_RESUME.stat().st_size // 1024),
                "uploaded_at": "", "source": "local file resume/resume.pdf"}
    return {}


def env_checks(s: Store) -> list[dict]:
    resume = resume_info(s)
    return [
        {"name": "Database", "ok": s.is_pg,
         "detail": "Neon/Postgres connected" if s.is_pg else
                   ("local SQLite (data/jobbot.db)" if not ON_VERCEL else "DATABASE_URL not set — data will be lost")},
        {"name": "Gmail", "ok": bool(os.getenv("GMAIL_USER") and os.getenv("GMAIL_APP_PASSWORD")),
         "detail": os.getenv("GMAIL_USER") or "set GMAIL_USER and GMAIL_APP_PASSWORD"},
        {"name": "Resume", "ok": bool(resume),
         "detail": resume.get("name") or "upload your PDF in the Job profile tab"},
        {"name": "Schedule secret", "ok": bool(CRON_SECRET) or not ON_VERCEL,
         "detail": "CRON_SECRET set" if CRON_SECRET else
                   ("set CRON_SECRET so Vercel can run the bot" if ON_VERCEL else "not needed locally")},
    ]


# ---------------------------------------------------------------- pages
@app.get("/")
@login_required
def index():
    s = store()
    raw = read_yaml(CONFIG_PATH)
    tz = raw.get("timezone", "Asia/Kolkata")
    now = datetime.now(ZoneInfo(tz))
    today = now.date().isoformat()

    try:
        profile, profile_error = get_profile(s, BASE / raw.get("profile_path", "job_profile.yaml")), ""
    except ConfigError as e:
        profile, profile_error = {}, str(e)

    # The openings list follows the current job profile; earlier ones are behind "Show previous".
    show_previous = request.args.get("previous") == "1"
    if profile:
        pid = profile_id(profile)
        s.adopt_untagged_openings(pid)
        current = s.openings(pid)
        previous = s.openings(pid, previous=True) if show_previous else []
        previous_count = s.count_previous_openings(pid)
    else:
        current, previous, previous_count = s.openings(), [], 0

    apps = s.applications()
    sent = [r for r in apps if r.get("status") == "sent"]
    emailed_urls = {r.get("source_url") for r in sent}

    for o in current + previous:
        o["safe_url"] = safe_url(o.get("url", ""))
        o["status"] = ("emailed" if o.get("url") in emailed_urls else
                       "manual" if o.get("manual_at") else "new")
    for a in apps:
        a["safe_url"] = safe_url(a.get("source_url", ""))
    openings = previous if show_previous else current

    settings = get_settings(s, raw)
    stats = {  # always about the current job profile
        "sent_total": len(sent),
        "sent_today": sum(1 for r in sent if (r["ts"] or "").startswith(today)),
        "daily_limit": settings["daily_limit"],
        "failed": sum(1 for r in apps if r.get("status") == "failed"),
        "openings": len(current),
        "openings_today": sum(1 for o in current if (o.get("found_at") or "").startswith(today)),
        "manual": sum(1 for o in current if o["status"] == "manual"),
        "new": sum(1 for o in current if o["status"] == "new"),
    }
    series = daily_series(apps, now.date())
    return render_template(
        "dashboard.html",
        stats=stats, series=series, series_max=max([d["count"] for d in series] + [1]),
        apps=list(reversed(apps)), openings=list(reversed(openings)),
        profile=profile, profile_error=profile_error, work_modes=WORK_MODES,
        settings=settings, status=s.get("status", {}), paused=bool(s.get("paused")),
        log_lines=s.log_tail(80), checks=env_checks(s), resume=resume_info(s), on_vercel=ON_VERCEL,
        auth_enabled=bool(PASSWORD), tab=request.args.get("tab", "overview"),
        show_previous=show_previous, previous_count=previous_count,
    )


@app.post("/profile")
@login_required
def update_profile():
    f = request.form
    data = {
        "job_description": f.get("job_description", ""),
        "role_titles": f.get("role_titles", ""),
        "keywords": f.get("keywords", ""),
        "exclude_keywords": f.get("exclude_keywords", ""),
        "experience": {"min": f.get("exp_min") or "0", "max": f.get("exp_max") or "50"},
        "locations": f.get("locations", ""),
        "work_modes": f.getlist("work_modes"),
        "min_score": f.get("min_score") or "0.4",
    }
    try:
        _, changed = save_profile(store(), data)
        if changed:
            flash("Job profile saved. The Job openings list now starts fresh for this profile; earlier "
                  "openings are kept under \"Show previous\". Press Dry run to search right away, "
                  "or the bot will search on its next scheduled run.", "ok")
        else:
            flash("Job profile saved (no changes).", "ok")
    except ConfigError as e:
        flash(f"Not saved: {e}", "error")
    return redirect(url_for("index", tab="profile"))


@app.post("/settings")
@login_required
def update_settings():
    f = request.form
    try:
        new = validate_settings({
            "interval_minutes": f.get("interval_minutes") or "60",
            "daily_limit": f.get("daily_limit") or "10",
            "test_recipient": f.get("test_recipient", ""),
            "active_hours_enabled": f.get("active_hours_enabled") == "on",
            "active_start": f.get("active_start", "09:00"),
            "active_end": f.get("active_end", "18:00"),
        })
        store().set("settings", new)
        flash("Settings saved.", "ok")
    except ConfigError as e:
        flash(f"Not saved: {e}", "error")
    return redirect(url_for("index", tab="settings"))


@app.post("/resume")
@login_required
def upload_resume():
    file = request.files.get("resume")
    data = file.read() if file else b""
    if not data:
        flash("Choose a PDF file first.", "error")
    elif not data.startswith(b"%PDF"):
        flash("That file is not a PDF.", "error")
    elif len(data) > MAX_RESUME_BYTES:
        flash("Resume must be under 3 MB.", "error")
    else:
        name = os.path.basename(file.filename or "resume.pdf")
        store().set("resume", {"name": name, "b64": base64.b64encode(data).decode(),
                               "size": len(data), "uploaded_at": local_now_str()})
        flash(f"Resume uploaded ({name}, {max(1, len(data) // 1024)} KB). It will be attached to every application.", "ok")
    return redirect(url_for("index", tab="profile"))


@app.get("/resume")
@login_required
def view_resume():
    """Open the resume that is attached to applications."""
    data, name = current_resume(store())
    if not data:
        return "No resume uploaded yet.", 404
    return Response(data, mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{name}"'})


@app.errorhandler(413)
def too_large(_e):
    flash("File too large; resume must be under 3 MB.", "error")
    return redirect(url_for("index", tab="profile"))


@app.post("/openings/mark")
@login_required
def mark_opening():
    url = request.form.get("url", "")
    if url:
        tz = ZoneInfo(read_yaml(CONFIG_PATH).get("timezone", "Asia/Kolkata"))
        store().mark_manual(url, datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S"))
        flash("Marked as applied manually.", "ok")
    return redirect(url_for("index", tab="openings"))


@app.post("/bot/<action>")
@login_required
def bot_action(action: str):
    s = store()
    if action == "pause":
        s.set("paused", True)
        flash("Bot paused. Scheduled runs will do nothing until you resume.", "ok")
    elif action == "resume":
        s.set("paused", False)
        flash("Bot resumed.", "ok")
    elif action in ("run", "dry-run"):
        result = run_once(s, CONFIG_PATH, dry_run=(action == "dry-run"), force=True)
        flash(f"Run finished: {result}", "ok" if not result.startswith(("Error", "Setup")) else "error")
    return redirect(url_for("index"))


@app.get("/export/<name>.csv")
@login_required
def export_csv(name: str):
    s = store()
    if name == "applications":
        rows, fields = s.applications(), APP_FIELDS
    elif name == "openings":
        rows, fields = s.openings(), OPENING_FIELDS
    else:
        return "Not found", 404
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={name}.csv"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Dashboard: http://127.0.0.1:{port}  (storage: {'Postgres' if database_url() else 'SQLite'})")
    app.run(host="127.0.0.1", port=port, debug=False)
