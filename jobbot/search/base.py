"""Shared types and helpers for search sources."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# Sites we never crawl or treat as "the company" (job boards, social media, aggregators).
NON_COMPANY_DOMAINS = {
    "linkedin.com", "indeed.com", "naukri.com", "glassdoor.com", "glassdoor.co.in",
    "monster.com", "foundit.in", "shine.com", "timesjobs.com", "instahyre.com",
    "wellfound.com", "angel.co", "cutshort.io", "hirist.tech", "iimjobs.com",
    "apna.co", "workindia.in", "internshala.com", "freshersworld.com", "simplyhired.com",
    "ziprecruiter.com", "remotive.com", "arbeitnow.com", "adzuna.com", "adzuna.in",
    "jooble.org", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "reddit.com", "quora.com", "medium.com", "wikipedia.org",
    "ambitionbox.com", "justdial.com", "crunchbase.com", "zaubacorp.com",
    "telegram.me", "t.me", "whatsapp.com", "google.com", "bing.com", "duckduckgo.com",
    "github.com", "stackoverflow.com", "scribd.com", "slideshare.net", "blogspot.com",
    "weekday.works", "sarthiq.com", "scowter.com", "acciojob.com", "talent.com", "jobrapido.com",
    "careerjet.co.in", "careerjet.com", "jora.com", "whatjobs.com", "jobsora.com", "trabajo.org",
    "updazz.com", "jobted.in", "hirect.in", "workingnomads.com", "weworkremotely.com",
    "remoteok.com", "himalayas.app", "builtin.com", "dice.com", "jobgether.com", "glints.com",
    "superset.com", "unstop.com", "placementindia.com", "olx.in", "quikr.com", "jobhai.com",
}

# Brand labels of the domains above, so e.g. simplyhired.co.in / indeed.co.in also match.
NON_COMPANY_LABELS = {d.split(".")[0] for d in NON_COMPANY_DOMAINS} - {"x", "t", "google"}

# Titles of search-result / listing pages rather than a single company's posting.
AGGREGATOR_TITLE = re.compile(
    r"\b\d[\d,]*\+?\s*(?:open\s+|latest\s+|new\s+)?(?:jobs|openings|vacancies|job openings)\b"
    r"|\bjobs\s+(?:in|for|at|near)\b|\bjob\s+search\b|\bjobs\s*[-–|(]|\bjobs\s*$"
    r"|\bvacancies\s+in\b|\bsalary\b|\binterview questions\b",
    re.IGNORECASE,
)

_MULTI_PART_TLDS = {"co.in", "co.uk", "com.au", "org.in", "net.in", "firm.in", "gen.in",
                    "ind.in", "co.jp", "com.sg", "co.nz", "com.br", "co.za"}


def root_domain(url_or_host: str) -> str:
    """'https://careers.acme.co.in/x' -> 'acme.co.in'."""
    host = url_or_host
    if "://" in host:
        host = urlparse(host).netloc
    host = host.split("@")[-1].split(":")[0].lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _MULTI_PART_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def is_non_company(url_or_host: str) -> bool:
    rd = root_domain(url_or_host)
    return rd in NON_COMPANY_DOMAINS or rd.split(".")[0] in NON_COMPANY_LABELS


def company_key(name: str) -> str:
    """Normalised company name used for de-duplication."""
    name = name.lower()
    name = re.sub(r"\b(pvt|private|ltd|limited|llp|inc|llc|technologies|technology|solutions|"
                  r"services|software|systems|india|the|co|corp|corporation)\b", " ", name)
    return re.sub(r"[^a-z0-9]", "", name)


@dataclass
class Lead:
    company: str
    title: str
    url: str
    snippet: str = ""
    source: str = ""
    emails: list[str] = field(default_factory=list)   # emails seen in the posting text itself
    company_site: str = ""                            # company website, if known
    role: str = ""                                    # best-matching configured role title
    score: float = 0.0

    @property
    def key(self) -> str:
        return company_key(self.company) or root_domain(self.company_site or self.url)
