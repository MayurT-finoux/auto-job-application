"""Record of applications emailed; also used so a company is never emailed twice."""
from __future__ import annotations

import logging

from .search.base import Lead, company_key, root_domain
from .store import Store

log = logging.getLogger(__name__)

FREE_MAIL = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "rediffmail.com"}


class AppLog:
    def __init__(self, store: Store, now_str):
        self.store = store
        self.now_str = now_str  # callable returning local time "YYYY-MM-DD HH:MM:SS"
        self.applied_emails: set[str] = set()
        self.applied_keys: set[str] = set()
        self.applied_domains: set[str] = set()
        for row in store.applications(status="sent"):
            self._remember(row.get("company") or "", row.get("email") or "")

    def _remember(self, company: str, email: str) -> None:
        if email:
            self.applied_emails.add(email.lower())
            dom = root_domain(email.split("@")[-1])
            if dom not in FREE_MAIL:
                self.applied_domains.add(dom)
        if company_key(company):
            self.applied_keys.add(company_key(company))

    def already_applied(self, lead: Lead, email: str | None = None) -> bool:
        if lead.key and lead.key in self.applied_keys:
            return True
        if email:
            if email.lower() in self.applied_emails:
                return True
            if root_domain(email.split("@")[-1]) in self.applied_domains:
                return True
        return False

    def sent_today(self) -> int:
        return self.store.sent_count_on(self.now_str()[:10])

    def record(self, lead: Lead, email: str, status: str, error: str = "") -> None:
        row = {
            "ts": self.now_str(), "company": lead.company, "role": lead.role or lead.title,
            "email": email, "source": lead.source, "source_url": lead.url, "score": lead.score,
            "status": status, "error": error[:500],
        }
        self.store.add_application(row)
        if status == "sent":
            self._remember(lead.company, email)
        log.info("[%s] %s | %s | %s | %s", status.upper(), lead.company, row["role"], email,
                 error or lead.url)
