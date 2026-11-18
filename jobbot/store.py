"""Persistent storage: Postgres (Neon, on Vercel) when DATABASE_URL is set, otherwise a local SQLite file.

Everything the bot remembers lives here: applications sent, job openings found, the job profile,
dashboard settings, the uploaded resume, the bot's work queue/status, and recent log lines.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

LOG_KEEP = 500  # bot log lines kept

APP_FIELDS = ["ts", "company", "role", "email", "source", "source_url", "score", "status", "error"]
OPENING_FIELDS = ["found_at", "company", "title", "matched_role", "experience", "work_mode", "source",
                  "score", "url", "email_in_post", "apply_via", "description", "manual_at", "profile_id"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id {pk}, ts TEXT NOT NULL, company TEXT, role TEXT, email TEXT, source TEXT,
    source_url TEXT, score REAL, status TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS applications_status ON applications(status);
CREATE TABLE IF NOT EXISTS openings (
    id {pk}, found_at TEXT NOT NULL, company TEXT, title TEXT, matched_role TEXT, experience TEXT,
    work_mode TEXT, source TEXT, score REAL, url TEXT NOT NULL UNIQUE, email_in_post TEXT,
    apply_via TEXT, description TEXT, manual_at TEXT, profile_id TEXT
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS bot_log (id {pk}, line TEXT NOT NULL);
"""


def database_url() -> str:
    # Neon's Vercel integration sets DATABASE_URL (and POSTGRES_URL); either works.
    return os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or ""


class Store:
    def __init__(self, url: str | None = None, sqlite_path: str | Path = "data/jobbot.db"):
        url = database_url() if url is None else url
        self.is_pg = url.startswith(("postgres://", "postgresql://"))
        if self.is_pg:
            import psycopg
            self.conn = psycopg.connect(url, autocommit=True, connect_timeout=15)
            pk = "BIGSERIAL PRIMARY KEY"
        else:
            Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(sqlite_path), timeout=30, isolation_level=None,
                                        check_same_thread=False)
            self.conn.execute("PRAGMA journal_mode=WAL")
            pk = "INTEGER PRIMARY KEY AUTOINCREMENT"
        for stmt in _SCHEMA.format(pk=pk).split(";"):
            if stmt.strip():
                self._exec(stmt)
        self._migrate()

    def _migrate(self) -> None:
        """Upgrade tables created by older versions."""
        cols = {r["name"] for r in self._rows(
            "SELECT column_name AS name FROM information_schema.columns WHERE table_name = 'openings'"
            if self.is_pg else "SELECT name FROM pragma_table_info('openings')")}
        if "profile_id" not in cols:
            self._exec("ALTER TABLE openings ADD COLUMN profile_id TEXT")
        self._exec("CREATE INDEX IF NOT EXISTS openings_profile ON openings(profile_id)")

    @property
    def kind(self) -> str:
        return "Postgres" if self.is_pg else "SQLite"

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------ low level
    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.is_pg else sql

    def _exec(self, sql: str, params: tuple | list = ()) -> int:
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), params)
        n = cur.rowcount
        cur.close()
        return n

    def _many(self, sql: str, rows: list[tuple]) -> None:
        if rows:
            cur = self.conn.cursor()
            cur.executemany(self._sql(sql), rows)
            cur.close()

    def _rows(self, sql: str, params: tuple | list = ()) -> list[dict]:
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), params)
        cols = [d[0] for d in cur.description]
        out = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        return out

    # ------------------------------------------------------------ key/value
    def get(self, key: str, default: Any = None) -> Any:
        rows = self._rows("SELECT value FROM kv WHERE key = ?", (key,))
        if not rows or rows[0]["value"] is None:
            return default
        try:
            return json.loads(rows[0]["value"])
        except ValueError:
            return default

    def set(self, key: str, value: Any) -> None:
        self._exec("INSERT INTO kv (key, value) VALUES (?, ?) "
                   "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))

    def delete(self, key: str) -> None:
        self._exec("DELETE FROM kv WHERE key = ?", (key,))

    # ------------------------------------------------------------ applications
    def add_application(self, row: dict) -> None:
        self._exec(f"INSERT INTO applications ({', '.join(APP_FIELDS)}) VALUES ({', '.join('?' * len(APP_FIELDS))})",
                   tuple(row.get(f) for f in APP_FIELDS))

    def applications(self, status: str | None = None) -> list[dict]:
        if status:
            return self._rows("SELECT * FROM applications WHERE status = ? ORDER BY id", (status,))
        return self._rows("SELECT * FROM applications ORDER BY id")

    def sent_count_on(self, day: str) -> int:
        rows = self._rows("SELECT COUNT(*) AS n FROM applications WHERE status = 'sent' AND ts LIKE ?",
                          (day + "%",))
        return int(rows[0]["n"])

    # ------------------------------------------------------------ openings
    def add_opening(self, row: dict) -> bool:
        """Save an opening for the current job profile. Returns True if it is new for that profile.

        A link already found under an earlier profile is moved to the current one (keeping any
        "applied manually" mark) so it shows up in the current list.
        """
        existing = self._rows("SELECT profile_id FROM openings WHERE url = ?", (row["url"],))
        if existing:
            if existing[0]["profile_id"] == row.get("profile_id"):
                return False
            self._exec("UPDATE openings SET profile_id = ?, found_at = ?, matched_role = ?, score = ? "
                       "WHERE url = ?", (row.get("profile_id"), row.get("found_at"), row.get("matched_role"),
                                         row.get("score"), row["url"]))
            return True
        fields = [f for f in OPENING_FIELDS if f != "manual_at"]
        n = self._exec(f"INSERT INTO openings ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))}) "
                       "ON CONFLICT (url) DO NOTHING", tuple(row.get(f) for f in fields))
        return n > 0

    def openings(self, profile_id: str | None = None, previous: bool = False) -> list[dict]:
        """All openings; or only the given profile's; or (previous=True) only other profiles'."""
        if profile_id is None:
            return self._rows("SELECT * FROM openings ORDER BY id")
        if previous:
            return self._rows("SELECT * FROM openings WHERE profile_id IS NULL OR profile_id <> ? ORDER BY id",
                              (profile_id,))
        return self._rows("SELECT * FROM openings WHERE profile_id = ? ORDER BY id", (profile_id,))

    def count_previous_openings(self, profile_id: str) -> int:
        rows = self._rows("SELECT COUNT(*) AS n FROM openings WHERE profile_id IS NULL OR profile_id <> ?",
                          (profile_id,))
        return int(rows[0]["n"])

    def adopt_untagged_openings(self, profile_id: str) -> None:
        """Openings saved before profiles were tracked belong to the profile in use at the time."""
        self._exec("UPDATE openings SET profile_id = ? WHERE profile_id IS NULL", (profile_id,))

    def mark_manual(self, url: str, when: str) -> bool:
        return self._exec("UPDATE openings SET manual_at = ? WHERE url = ?", (when, url)) > 0

    # ------------------------------------------------------------ bot log
    def add_log_lines(self, lines: list[str]) -> None:
        if not lines:
            return
        self._many("INSERT INTO bot_log (line) VALUES (?)", [(l,) for l in lines])
        self._exec("DELETE FROM bot_log WHERE id <= (SELECT MAX(id) FROM bot_log) - ?", (LOG_KEEP,))

    def log_tail(self, n: int = 60) -> list[str]:
        rows = self._rows("SELECT line FROM bot_log ORDER BY id DESC LIMIT ?", (n,))
        return [r["line"] for r in reversed(rows)]
