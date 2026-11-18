"""DuckDuckGo web search (free, no API key) via the `ddgs` package."""
from __future__ import annotations

import logging
import random
import re
import time

from ..email_finder import extract_emails
from .base import AGGREGATOR_TITLE, Lead, is_non_company, root_domain

log = logging.getLogger(__name__)

# {role} and {loc} are filled in. Most queries find company careers pages / postings; the
# email is then found by crawling the company's own website. A few look for posts that print an
# email directly ("send your CV to hr@...").
QUERY_TEMPLATES = [
    "{role} job opening {loc} careers",
    "{role} hiring {loc} company careers page",
    "{role} vacancy {loc} apply now",
    "{role} careers {loc} join our team",
    "{role} hiring {loc} send resume email",
    "{role} {loc} we are hiring email cv",
    "{role} {loc} linkedin job hiring",
    "{role} {loc} naukri job",
]

# "Acme hiring Python Developer in Mumbai | LinkedIn", "Python Developer at Acme - Pune"
_TITLE_PATTERNS = [
    re.compile(r"^(?P<company>.+?)\s+(?:is\s+)?hiring\b", re.IGNORECASE),
    re.compile(r"\b(?:at|@)\s+(?P<company>[A-Z][\w&.\- ]{1,40}?)(?:\s+[-|,(]|\s+in\s|$)"),
    re.compile(r"^.+?\s+[-|]\s+(?P<company>[A-Z][\w&.\- ]{1,40}?)\s+[-|]"),
]


def guess_company(title: str, href: str, emails: list[str]) -> tuple[str, str]:
    """Return (company name, company site) best-effort."""
    for e in emails:
        dom = e.split("@")[1]
        if dom not in ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "rediffmail.com"):
            rd = root_domain(dom)
            return rd.split(".")[0].capitalize(), f"https://{rd}"
    if href and not is_non_company(href):
        rd = root_domain(href)
        return rd.split(".")[0].capitalize(), f"https://{rd}"
    for pat in _TITLE_PATTERNS:
        m = pat.search(title or "")
        if m:
            name = m.group("company").strip(" -|")
            if 1 < len(name) < 50 and not re.search(r"linkedin|naukri|indeed|jobs?\b", name, re.I):
                return name, ""
    return "", ""


def search(role_titles: list[str], locations: list[str], max_queries: int = 8,
           results_per_query: int = 20) -> list[Lead]:
    try:
        from ddgs import DDGS
    except ImportError:
        log.warning("ddgs not installed; skipping DuckDuckGo")
        return []

    combos = [(t, r, l) for t in QUERY_TEMPLATES for r in role_titles for l in (locations or [""])]
    random.shuffle(combos)
    leads: list[Lead] = []
    ddg = DDGS()
    failures = 0
    for tpl, role, loc in combos[:max_queries]:
        query = tpl.format(role=role, loc=loc).strip()
        try:
            results = ddg.text(query, region="in-en", max_results=results_per_query) or []
        except Exception as e:  # rate limit / network
            failures += 1
            log.warning("DuckDuckGo query failed (%s): %s", query, e)
            if failures >= 3:
                log.warning("DuckDuckGo looks rate-limited; skipping it for this round")
                break
            time.sleep(5)
            continue
        failures = 0
        for r in results:
            title, href, body = r.get("title", ""), r.get("href", ""), r.get("body", "")
            emails = extract_emails(f"{title} {body}")
            if not emails and AGGREGATOR_TITLE.search(title):
                continue  # a job-listing page, not a single company's opening
            company, site = guess_company(title, href, emails)
            if not company:
                continue
            leads.append(Lead(company=company, title=title, url=href, snippet=body,
                              source="duckduckgo", emails=emails, company_site=site))
        time.sleep(random.uniform(1.5, 3.5))  # be polite
    log.info("DuckDuckGo: %d raw leads", len(leads))
    return leads
