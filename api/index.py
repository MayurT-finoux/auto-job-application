"""Vercel entry point: serves the Flask dashboard and the /api/run endpoint."""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# show bot logs in Vercel's function logs
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
for noisy in ("urllib3", "primp", "httpx", "ddgs", "h2", "hpack", "rquest", "cookie_store", "psycopg"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

from dashboard import app  # noqa: E402,F401
