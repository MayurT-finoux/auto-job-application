"""Find and rank a contact email for a lead."""
from __future__ import annotations

import logging
import re
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .search.base import USER_AGENT, Lead, is_non_company, root_domain

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# "hr [at] acme [dot] com", "hr(at)acme(dot)com"
OBFUSCATED_RE = re.compile(
    r"([A-Za-z0-9._%+-]+)\s*[\[(]\s*at\s*[\])]\s*([A-Za-z0-9-]+(?:\s*[\[(]\s*dot\s*[\])]\s*[A-Za-z0-9-]+)+)",
    re.IGNORECASE,
)

FREE_MAIL = {"gmail.com", "yahoo.com", "yahoo.co.in", "outlook.com", "hotmail.com",
             "rediffmail.com", "live.com", "icloud.com", "protonmail.com", "zoho.com"}

BAD_LOCAL = re.compile(r"(no-?reply|donotreply|do-not-reply|mailer-daemon|postmaster|abuse|privacy|"
                       r"gdpr|legal|unsubscribe|webmaster|example|test|sentry|billing|invoice|"
                       r"press|media|investor|grievance|complaint)", re.IGNORECASE)
BAD_DOMAIN = re.compile(r"(example\.|sentry|wixpress|domain\.com|email\.com|yourcompany|company\.com|"
                        r"sentry-next|godaddy|cloudflare)", re.IGNORECASE)
FILE_EXT = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js|ico|pdf|woff2?)$", re.IGNORECASE)

# Lower index = better.
PREFERENCE = [
    r"^(hr|h\.r)\b", r"^careers?\b", r"^jobs?\b", r"recruit", r"^talent", r"^hiring",
    r"^hr[._-]", r"^ta\b", r"people", r"^resume", r"^cv\b", r"^apply", r"^work", r"^join",
    r"^info\b", r"^contact", r"^hello", r"^office", r"^admin", r"^enquir", r"^support",
]

CRAWL_PATHS = ["", "/careers", "/career", "/jobs", "/contact", "/contact-us", "/about", "/about-us"]
TIMEOUT = 6


def extract_emails(text: str) -> list[str]:
    text = unquote(text or "")
    found = [m.group(0) for m in EMAIL_RE.finditer(text)]
    for m in OBFUSCATED_RE.finditer(text):
        domain = re.sub(r"\s*[\[(]\s*dot\s*[\])]\s*", ".", m.group(2), flags=re.IGNORECASE)
        found.append(f"{m.group(1)}@{domain}")
    out: list[str] = []
    for e in found:
        e = e.strip(".-_").lower()
        if e not in out and is_valid_email(e):
            out.append(e)
    return out


def is_valid_email(email: str) -> bool:
    if "@" not in email or FILE_EXT.search(email):
        return False
    local, domain = email.rsplit("@", 1)
    if not local or len(local) > 40 or re.fullmatch(r"[0-9a-f]{16,}", local):
        return False
    if BAD_LOCAL.search(local) or BAD_DOMAIN.search(domain):
        return False
    if is_non_company(domain):
        return False
    return True


def _rank(email: str) -> int:
    local = email.split("@")[0]
    for i, pat in enumerate(PREFERENCE):
        if re.search(pat, local):
            return i
    return len(PREFERENCE)


def choose_email(emails: list[str], company_domain: str = "", trusted: bool = False) -> str | None:
    """Pick the best email.

    trusted=True means the emails came from the posting text itself (e.g. "send CV to x@gmail.com"),
    so free-mail / any-domain addresses are allowed. Otherwise only addresses on the company's own
    domain are accepted.
    """
    candidates = []
    for e in emails:
        dom = root_domain(e.split("@")[1])
        if not trusted:
            if not company_domain or dom != root_domain(company_domain):
                continue
        candidates.append(e)
    if not candidates:
        return None
    return sorted(candidates, key=lambda e: (_rank(e), len(e)))[0]


# ---------------------------------------------------------------- network helpers

def _get(url: str) -> str:
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, allow_redirects=True)
        if r.ok and "text/html" in r.headers.get("content-type", "text/html"):
            return r.text
    except requests.RequestException as e:
        log.debug("fetch failed %s: %s", url, e)
    return ""


def _emails_from_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    mailtos = [a["href"][7:].split("?")[0] for a in soup.find_all("a", href=True)
               if a["href"].lower().startswith("mailto:")]
    return extract_emails(" ".join(mailtos) + " " + soup.get_text(" "))


def crawl_company_site(site: str) -> list[str]:
    """Fetch a few likely pages of a company website and collect emails."""
    parsed = urlparse(site if "://" in site else f"https://{site}")
    base = f"{parsed.scheme or 'https'}://{parsed.netloc}"
    emails: list[str] = []
    visited: set[str] = set()
    extra: list[str] = []
    for path in CRAWL_PATHS:
        url = urljoin(base, path)
        if url in visited:
            continue
        visited.add(url)
        html = _get(url)
        if not html:
            continue
        for e in _emails_from_html(html):
            if e not in emails:
                emails.append(e)
        if path == "":
            # follow links on the homepage that look like careers/contact pages
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if re.search(r"career|contact|job|join", href, re.IGNORECASE):
                    full = urljoin(base, href)
                    if root_domain(full) == root_domain(base) and full not in extra:
                        extra.append(full)
        if any(_rank(e) < 6 for e in emails):  # found an HR-type address, stop early
            break
    for url in extra[:3]:
        if url in visited:
            continue
        visited.add(url)
        for e in _emails_from_html(_get(url)):
            if e not in emails:
                emails.append(e)
    return emails


def find_company_site(company: str) -> str:
    """Use a web search to guess a company's official website."""
    if not company:
        return ""
    try:
        from ddgs import DDGS
        results = DDGS().text(f"{company} official website", max_results=8)
    except Exception as e:  # network / rate-limit
        log.debug("site lookup failed for %s: %s", company, e)
        return ""
    for r in results or []:
        href = r.get("href", "")
        if href and not is_non_company(href):
            return href
    return ""


def resolve_email(lead: Lead) -> str | None:
    """Return the best contact email for a lead, crawling the company website if needed."""
    # 1. Emails printed in the posting/snippet are the strongest signal.
    best = choose_email(lead.emails, trusted=True)
    if best:
        return best

    # 2. Fetch the posting page itself (unless it's a job board we shouldn't scrape).
    if lead.url and not is_non_company(lead.url):
        page_emails = _emails_from_html(_get(lead.url))
        best = choose_email(page_emails, trusted=True)
        if best:
            return best
        if not lead.company_site:
            lead.company_site = lead.url

    # 3. Crawl the company's website.
    if not lead.company_site:
        lead.company_site = find_company_site(lead.company)
    if lead.company_site:
        site_emails = crawl_company_site(lead.company_site)
        return choose_email(site_emails, company_domain=lead.company_site)
    return None
