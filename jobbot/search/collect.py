"""Run every enabled source and merge the results."""
from __future__ import annotations

import logging

from ..config import Config
from . import adzuna, duckduckgo, jooble, remote_boards
from .base import Lead

log = logging.getLogger(__name__)


def gather_leads(cfg: Config) -> list[Lead]:
    src = cfg.sources
    leads: list[Lead] = []
    if src.get("duckduckgo", True):
        leads += duckduckgo.search(cfg.role_titles, cfg.search_locations, cfg.max_queries_per_round)
    if src.get("remotive") and cfg.remote_ok:
        leads += remote_boards.remotive(cfg.role_titles)
    if src.get("arbeitnow"):
        leads += remote_boards.arbeitnow(cfg.role_titles)
    if src.get("adzuna"):
        if cfg.env.get("ADZUNA_APP_ID") and cfg.env.get("ADZUNA_APP_KEY"):
            leads += adzuna.search(cfg.env["ADZUNA_APP_ID"], cfg.env["ADZUNA_APP_KEY"],
                                   cfg.role_titles, cfg.search_locations, src.get("adzuna_country", "in"))
        else:
            log.warning("Adzuna enabled but ADZUNA_APP_ID/ADZUNA_APP_KEY missing in .env")
    if src.get("jooble"):
        if cfg.env.get("JOOBLE_API_KEY"):
            leads += jooble.search(cfg.env["JOOBLE_API_KEY"], cfg.role_titles, cfg.search_locations)
        else:
            log.warning("Jooble enabled but JOOBLE_API_KEY missing in .env")
    return [l for l in leads if l.company.strip()]
