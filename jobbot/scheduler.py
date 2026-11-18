"""One bot run = at most one application.

Vercel Schedules (or the local runner in main.py) call `run_once` every 15 minutes. A run only sends
when `interval_minutes` have passed since the last email, so the frequency is adjustable from the
dashboard. All state (queue, status, log) lives in the store because each run is a fresh process.
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import mailer
from .applog import AppLog
from .config import Config, ConfigError, load_config, profile_id, read_yaml
from .email_finder import resolve_email
from .matcher import Matcher
from .openings import record_opening
from .search.base import Lead
from .search.collect import gather_leads
from .store import Store

log = logging.getLogger("jobbot")
log.setLevel(logging.INFO)

RUN_BUDGET_SECONDS = 170          # stop trying new leads after this (Vercel Hobby hard limit is 300s)
MIN_SEARCH_GAP_SECONDS = 45 * 60  # don't re-run web searches more often than this
SLACK_SECONDS = 5 * 60            # scheduler jitter tolerance when checking the interval
LOCK_TTL_SECONDS = 6 * 60
MAX_SEEN = 3000


class StoreLogHandler(logging.Handler):
    """Buffers log lines during a run and writes them to the store at the end (one round trip)."""

    def __init__(self, now_str):
        super().__init__(logging.INFO)
        self.now_str = now_str
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(f"{self.now_str()} {record.levelname:<7} {record.getMessage()}")


class Bot:
    def __init__(self, cfg: Config, store: Store, dry_run: bool = False, deadline: float | None = None):
        self.cfg = cfg
        self.store = store
        self.dry_run = dry_run
        self.deadline = deadline or (time.time() + RUN_BUDGET_SECONDS)
        self.matcher = Matcher(cfg)
        self.applog = AppLog(store, cfg.now_str)
        self.profile_id = profile_id(store.get("profile"))
        store.adopt_untagged_openings(self.profile_id)
        # persisted work queue
        self.queue: list[Lead] = [Lead(**d) for d in store.get("queue", [])]
        self.seen: list[str] = store.get("seen", [])
        self.last_search: float = store.get("last_search", 0.0)
        # a changed job profile invalidates the queue
        sig = [cfg.role_titles, cfg.keywords, cfg.exclude_keywords, cfg.exp_min, cfg.exp_max,
               cfg.locations, cfg.work_modes, cfg.min_score]
        if store.get("queue_profile") != sig:
            self.queue, self.seen, self.last_search = [], [], 0.0
            store.set("queue_profile", sig)

    def save_state(self) -> None:
        self.store.set("queue", [asdict(l) for l in self.queue])
        self.store.set("seen", self.seen[-MAX_SEEN:])
        self.store.set("last_search", self.last_search)

    def time_left(self) -> bool:
        return time.time() < self.deadline

    # ------------------------------------------------------------ search
    def refill(self) -> int:
        if self.last_search and time.time() - self.last_search < MIN_SEARCH_GAP_SECONDS:
            return 0
        self.last_search = time.time()
        log.info("Searching for new openings...")
        seen = set(self.seen)
        added = saved = 0
        found_at = self.cfg.now_str()
        for lead in gather_leads(self.cfg):
            if not self.matcher.accept(lead):
                continue
            if record_opening(self.store, lead, found_at, self.profile_id):
                saved += 1
            if not lead.key or lead.key in seen or self.applog.already_applied(lead):
                continue
            seen.add(lead.key)
            self.seen.append(lead.key)
            self.queue.append(lead)
            added += 1
        # Leads that already show an email in the posting go first.
        self.queue.sort(key=lambda l: (not l.emails, -l.score))
        log.info("Search done: %d new openings saved, %d leads queued for email (queue %d)",
                 saved, added, len(self.queue))
        self.save_state()
        return added

    def next_application(self) -> tuple[Lead, str] | None:
        while self.time_left():
            if not self.queue and not self.refill():
                return None
            if not self.time_left():
                break
            lead = self.queue.pop(0)
            self.save_state()
            if self.applog.already_applied(lead):
                continue
            email = resolve_email(lead)
            if not email:
                log.info("No contact email for %s, skipping", lead.company)
                continue
            if self.applog.already_applied(lead, email):
                continue
            return lead, email
        log.info("Run time budget used up; continuing next run")
        return None

    # ------------------------------------------------------------ guards
    def in_active_hours(self) -> bool:
        ah = self.cfg.active_hours
        if not ah:
            return True
        now = self.cfg.now().strftime("%H:%M")
        start, end = ah["start"], ah["end"]
        return start <= now <= end if start <= end else (now >= start or now <= end)

    def due_in_seconds(self) -> float:
        last = self.store.get("last_send_epoch", 0.0)
        return max(0.0, last + self.cfg.interval_minutes * 60 - SLACK_SECONDS - time.time())

    # ------------------------------------------------------------ one run
    def tick(self, force: bool = False) -> str:
        if not force:
            wait = self.due_in_seconds()
            if wait > 0:
                return f"Not due yet; next application in about {int(wait // 60) + 1} min"
            if not self.in_active_hours():
                return "Outside active hours"
        if self.applog.sent_today() >= self.cfg.daily_limit:
            return f"Daily limit ({self.cfg.daily_limit}) reached"
        nxt = self.next_application()
        if not nxt:
            return "No new lead with a contact email yet; will keep searching"
        lead, email = nxt
        lead.role = mailer.email_role(self.cfg, lead)
        msg = mailer.build_message(self.cfg, lead, email)
        if self.dry_run:
            body = msg.get_body(preferencelist=("plain",)).get_content()
            log.info("DRY RUN email to %s | Subject: %s\n%s", msg["To"], msg["Subject"], body)
            self.applog.record(lead, email, "dry-run")
            return f"Dry run: would email {email} ({lead.company})"
        self.store.set("last_send_epoch", time.time())
        try:
            mailer.send(self.cfg, msg)
            self.applog.record(lead, email, "sent")
            return f"Sent application to {email} ({lead.company})"
        except Exception as e:
            self.applog.record(lead, email, "failed", str(e))
            return f"Sending to {email} failed: {e}"


def _acquire_lock(store: Store) -> bool:
    held = store.get("run_lock", 0.0)
    if held and time.time() - held < LOCK_TTL_SECONDS:
        return False
    token = time.time()
    store.set("run_lock", token)
    return store.get("run_lock") == token


def run_once(store: Store, config_path: str | Path = "config.yaml", dry_run: bool = False,
             force: bool = False) -> str:
    """Run the bot once. `force` ignores pause, interval and active hours (used by "Run now")."""
    started = time.time()
    try:
        tz = ZoneInfo(read_yaml(Path(config_path)).get("timezone", "Asia/Kolkata"))
    except Exception:
        tz = ZoneInfo("Asia/Kolkata")
    local_now = lambda: datetime.now(tz).replace(tzinfo=None)
    handler = StoreLogHandler(lambda: local_now().strftime("%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)
    status = store.get("status", {})
    cfg = None
    result = ""
    try:
        if store.get("paused") and not force:
            result = "Paused from the dashboard"
            return result
        if not _acquire_lock(store):
            result = "Another run is in progress"
            return result
        try:
            cfg = load_config(config_path, store, require_mail=not dry_run)
            store.set("status", {**status, "state": "running", "updated": cfg.now_str()})
            bot = Bot(cfg, store, dry_run=dry_run, deadline=started + RUN_BUDGET_SECONDS)
            result = bot.tick(force=force)
            bot.save_state()
        except ConfigError as e:
            result = f"Setup needed: {e}"
        except Exception as e:  # never let one bad run kill the schedule
            log.exception("Run failed")
            result = f"Error: {e}"
        finally:
            store.delete("run_lock")
        log.info(result)
        return result
    finally:
        log.removeHandler(handler)
        now = local_now()
        if cfg and not store.get("paused"):
            last = store.get("last_send_epoch", 0.0)
            wait = max(0.0, last + cfg.interval_minutes * 60 - time.time())
            next_due = (now + timedelta(seconds=wait)).strftime("%Y-%m-%d %H:%M")
        else:
            next_due = ""
        store.set("status", {
            "state": "paused" if store.get("paused") else "idle",
            "last_run": now.strftime("%Y-%m-%d %H:%M:%S"),
            "last_result": result,
            "duration_s": round(time.time() - started, 1),
            "next_due": next_due,
            "queue": len(store.get("queue", [])),
            "dry_run": dry_run,
        })
        try:
            store.add_log_lines(handler.lines)
        except Exception:
            pass
