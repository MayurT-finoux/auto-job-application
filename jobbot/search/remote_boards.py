"""Free public job-board APIs that need no key: Remotive and Arbeitnow."""
from __future__ import annotations

import logging
import re

import requests

from ..email_finder import extract_emails
from .base import USER_AGENT, Lead

log = logging.getLogger(__name__)


def _strip_html(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or ""))


def remotive(role_titles: list[str]) -> list[Lead]:
    """https://remotive.com/api/remote-jobs — please keep usage light (a few calls/day)."""
    leads = []
    for role in role_titles:
        try:
            r = requests.get("https://remotive.com/api/remote-jobs", params={"search": role, "limit": 50},
                             headers={"User-Agent": USER_AGENT}, timeout=20)
            r.raise_for_status()
            jobs = r.json().get("jobs", [])
        except Exception as e:
            log.warning("Remotive failed: %s", e)
            continue
        for j in jobs:
            text = _strip_html(j.get("description", ""))
            leads.append(Lead(company=j.get("company_name", ""), title=j.get("title", ""),
                              url=j.get("url", ""), snippet=text[:3000], source="remotive",
                              emails=extract_emails(text)))
    log.info("Remotive: %d raw leads", len(leads))
    return leads


def arbeitnow(role_titles: list[str]) -> list[Lead]:
    """https://www.arbeitnow.com/api/job-board-api — no search param, filtered locally by matcher."""
    leads = []
    for page in (1, 2):
        try:
            r = requests.get("https://www.arbeitnow.com/api/job-board-api", params={"page": page},
                             headers={"User-Agent": USER_AGENT}, timeout=20)
            r.raise_for_status()
            jobs = r.json().get("data", [])
        except Exception as e:
            log.warning("Arbeitnow failed: %s", e)
            break
        for j in jobs:
            text = _strip_html(j.get("description", ""))
            leads.append(Lead(company=j.get("company_name", ""), title=j.get("title", ""),
                              url=j.get("url", ""), snippet=text[:3000], source="arbeitnow",
                              emails=extract_emails(text)))
    log.info("Arbeitnow: %d raw leads", len(leads))
    return leads
