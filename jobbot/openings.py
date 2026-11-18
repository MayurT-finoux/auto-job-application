"""Every matching job opening found, so you can also apply manually (e.g. on LinkedIn/Naukri)."""
from __future__ import annotations

from .matcher import parse_experience
from .search.base import Lead, is_non_company
from .store import Store


def work_mode(text: str) -> str:
    t = text.lower()
    if "hybrid" in t:
        return "hybrid"
    if "remote" in t or "work from home" in t or "wfh" in t:
        return "remote"
    return ""


def opening_row(lead: Lead, found_at: str, profile_id: str = "") -> dict:
    text = f"{lead.title} {lead.snippet}"
    rng = parse_experience(text)
    exp = "" if not rng else (f"{rng[0]:g}+ yrs" if rng[1] >= 99 else f"{rng[0]:g}-{rng[1]:g} yrs")
    return {
        "found_at": found_at, "company": lead.company, "title": lead.title[:200],
        "matched_role": lead.role, "experience": exp, "work_mode": work_mode(text),
        "source": lead.source, "score": lead.score, "url": lead.url,
        "email_in_post": ";".join(lead.emails[:3]),
        # job boards (LinkedIn, Naukri...) need a manual application on the site
        "apply_via": "job board (manual)" if is_non_company(lead.url) else "company site / email",
        "description": " ".join(lead.snippet.split())[:400],
        "profile_id": profile_id or None,
    }


def record_opening(store: Store, lead: Lead, found_at: str, profile_id: str = "") -> bool:
    if not lead.url:
        return False
    return store.add_opening(opening_row(lead, found_at, profile_id))
