"""Run the bot locally (on Vercel, Schedules call /api/run instead).

    python main.py --dry-run --once   # one forced run that prints the email instead of sending it
    python main.py --once             # one forced run: send one application now, then exit
    python main.py                    # keep running: check every 15 min, send when the interval is due

Uses Postgres if DATABASE_URL is set (e.g. your Neon database), otherwise data/jobbot.db (SQLite).
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from jobbot.scheduler import run_once
from jobbot.store import Store

CHECK_EVERY_MINUTES = 15


def _raise_interrupt(*_):
    raise KeyboardInterrupt


def main() -> int:
    p = argparse.ArgumentParser(description="Search for matching jobs and email applications.")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--dry-run", action="store_true", help="log emails instead of sending them")
    p.add_argument("--once", action="store_true", help="run once now (ignores interval/pause) and exit")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    for noisy in ("urllib3", "primp", "httpx", "ddgs", "h2", "hpack", "rquest", "cookie_store", "psycopg"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    signal.signal(signal.SIGTERM, _raise_interrupt)

    base = Path(args.config).resolve().parent
    store = Store(sqlite_path=base / "data" / "jobbot.db")
    logging.info("Storage: %s", store.kind)
    try:
        if args.once:
            run_once(store, args.config, dry_run=args.dry_run, force=True)
            return 0
        logging.info("Bot running; checking every %d min (Ctrl+C to stop)", CHECK_EVERY_MINUTES)
        while True:
            run_once(store, args.config, dry_run=args.dry_run)
            time.sleep(CHECK_EVERY_MINUTES * 60)
    except KeyboardInterrupt:
        logging.info("Bot stopped.")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
