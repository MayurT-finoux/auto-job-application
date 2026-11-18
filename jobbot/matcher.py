"""Score a lead against the target job description and experience range."""
from __future__ import annotations

import re
from collections import Counter

from .search.base import Lead

STOPWORDS = set("""
a an the and or of to in on for with as at by from is are be will we you your our this that these
those have has had it its experience years year looking candidate candidates role job work team
strong good knowledge ability skills required requirements plus preferred must should can using
including etc who what about into across within responsibilities responsible develop developer
developing build building based new well also other more any all their them they us
""".split())

_NUM = r"(\d{1,2}(?:\.\d)?)"
_YRS = r"\s*\+?\s*(?:years?|yrs?)"
EXP_PATTERNS = [
    (re.compile(_NUM + r"\s*(?:-|–|—|to)\s*" + _NUM + _YRS, re.I), "range"),
    (re.compile(r"(?:minimum|min\.?|at\s*least|atleast)\s*(?:of\s*)?" + _NUM + _YRS, re.I), "min"),
    (re.compile(_NUM + r"\s*\+" + r"\s*(?:years?|yrs?)", re.I), "min"),
    (re.compile(_NUM + r"\s*(?:years?|yrs?)\s*(?:of\s*)?(?:relevant\s*|professional\s*|work\s*)?(?:experience|exp)", re.I), "min"),
]


def parse_experience(text: str) -> tuple[float, float] | None:
    """Return the (min, max) years a posting asks for, or None if not stated."""
    for pat, kind in EXP_PATTERNS:
        m = pat.search(text or "")
        if not m:
            continue
        lo = float(m.group(1))
        hi = float(m.group(2)) if kind == "range" else 99.0
        if lo > 40 or hi < lo:
            continue
        return lo, hi
    return None


def experience_ok(text: str, exp_min: float, exp_max: float) -> bool:
    rng = parse_experience(text)
    if rng is None:
        return True
    lo, hi = rng
    return lo <= exp_max and hi >= exp_min


def jd_keywords(job_description: str, top_n: int = 8) -> list[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9+#.]{1,}", job_description.lower())
    tokens = [t.strip(".") for t in tokens if t.strip(".") not in STOPWORDS and len(t) > 2]
    return [w for w, _ in Counter(tokens).most_common(top_n)]


def _contains(text: str, term: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])", text) is not None


def best_role(text: str, role_titles: list[str]) -> tuple[str, float]:
    """Return the configured role that best matches the text, with a 0..1 score."""
    best, best_score = role_titles[0], 0.0
    for role in role_titles:
        if _contains(text, role):
            return role, 1.0
        words = [w for w in re.findall(r"[a-z0-9+#]+", role.lower()) if w not in STOPWORDS]
        if not words:
            continue
        s = sum(_contains(text, w) for w in words) / len(words)
        if s > best_score:
            best, best_score = role, s
    return best, best_score


def score_lead(lead: Lead, role_titles: list[str], keywords: list[str]) -> tuple[str, float]:
    text = f"{lead.title} {lead.snippet}".lower()
    role, role_score = best_role(text, role_titles)
    if keywords:
        hits = sum(_contains(text, k) for k in keywords)
        kw_score = min(1.0, hits / min(3, len(keywords)))
    else:
        kw_score = 1.0
    return role, round(0.5 * role_score + 0.5 * kw_score, 3)


class Matcher:
    def __init__(self, cfg):
        self.cfg = cfg
        kws = list(dict.fromkeys(cfg.keywords + jd_keywords(cfg.job_description)))
        self.keywords = kws

    def accept(self, lead: Lead) -> bool:
        role, score = score_lead(lead, self.cfg.role_titles, self.keywords)
        lead.role, lead.score = role, score
        if score < self.cfg.min_score:
            return False
        title = lead.title.lower()
        if any(_contains(title, k) for k in self.cfg.exclude_keywords):
            return False
        text = f"{lead.title} {lead.snippet}"
        is_remote = lead.source == "remotive" or _contains(title, "remote")
        if is_remote and not self.cfg.remote_ok:
            return False
        if self.cfg.work_modes == ["remote"] and not (is_remote or _contains(text.lower(), "remote")):
            return False
        return experience_ok(text, self.cfg.exp_min, self.cfg.exp_max)
