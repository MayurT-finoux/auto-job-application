"""Vercel entrypoint: Vercel serves the Flask `app` defined here (dashboard + /api/run).

Locally you can still run `python dashboard.py`.
"""
import logging

# show bot logs in Vercel's runtime logs
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
for noisy in ("urllib3", "primp", "httpx", "ddgs", "h2", "hpack", "rquest", "cookie_store", "psycopg"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

from dashboard import app  # noqa: E402,F401
