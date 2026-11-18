"""Adzuna job search API (free key: https://developer.adzuna.com/)."""
from __future__ import annotations

import logging

import requests

from ..email_finder import extract_emails
from .base import Lead

log = logging.getLogger(__name__)


def search(app_id: str, app_key: str, role_titles: list[str], locations: list[str],
           country: str = "in") -> list[Lead]:
    leads = []
    for role in role_titles:
        for loc in locations or [""]:
            params = {"app_id": app_id, "app_key": app_key, "what": role, "results_per_page": 50,
                      "content-type": "application/json"}
            if loc and loc.lower() != "remote":
                params["where"] = loc
            try:
                r = requests.get(f"https://api.adzuna.com/v1/api/jobs/{country}/search/1",
                                 params=params, timeout=20)
                r.raise_for_status()
                results = r.json().get("results", [])
            except Exception as e:
                log.warning("Adzuna failed (%s, %s): %s", role, loc, e)
                continue
            for j in results:
                desc = j.get("description", "")
                leads.append(Lead(company=(j.get("company") or {}).get("display_name", ""),
                                  title=j.get("title", ""), url=j.get("redirect_url", ""),
                                  snippet=desc, source="adzuna", emails=extract_emails(desc)))
    log.info("Adzuna: %d raw leads", len(leads))
    return leads
