"""Jooble job search API (free key: https://jooble.org/api/about)."""
from __future__ import annotations

import logging
import re

import requests

from ..email_finder import extract_emails
from .base import Lead

log = logging.getLogger(__name__)


def search(api_key: str, role_titles: list[str], locations: list[str]) -> list[Lead]:
    leads = []
    for role in role_titles:
        for loc in locations or [""]:
            try:
                r = requests.post(f"https://jooble.org/api/{api_key}",
                                  json={"keywords": role, "location": loc}, timeout=20)
                r.raise_for_status()
                jobs = r.json().get("jobs", [])
            except Exception as e:
                log.warning("Jooble failed (%s, %s): %s", role, loc, e)
                continue
            for j in jobs:
                snippet = re.sub(r"<[^>]+>", " ", j.get("snippet", ""))
                leads.append(Lead(company=j.get("company", ""), title=j.get("title", ""),
                                  url=j.get("link", ""), snippet=snippet, source="jooble",
                                  emails=extract_emails(snippet)))
    log.info("Jooble: %d raw leads", len(leads))
    return leads
