"""Trackers capability — Ace's gym/habits/journal transition here (NOT agents): a real
Postgres-backed timestamped log keyed by category. log(category, entry) + recent(category).
Ungated, no SQLite. One table covers gym/habits/journal/anything.
"""
from __future__ import annotations

import logging

import psycopg

from utah import UtahError, config

log = logging.getLogger("utah.product.trackers")

_DDL = """
CREATE TABLE IF NOT EXISTS tracker_entries (
  id bigserial PRIMARY KEY,
  category text NOT NULL,
  entry text NOT NULL,
  ts timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tracker_cat_ts ON tracker_entries (category, ts DESC);
"""


class TrackerError(UtahError):
    """Tracker store unreachable."""


def _conn():
    try:
        return psycopg.connect(config.DB_DSN, autocommit=True)
    except psycopg.Error as exc:
        raise TrackerError(f"tracker store unreachable: {exc}") from exc


def init_schema() -> None:
    with _conn() as c:
        c.execute(_DDL)


def log_entry(category: str, entry: str) -> int:
    category = (category or "").strip() or "journal"
    entry = (entry or "").strip()
    if not entry:
        raise TrackerError("empty entry")
    with _conn() as c:
        row = c.execute("INSERT INTO tracker_entries (category, entry) VALUES (%s,%s) RETURNING id",
                        (category, entry)).fetchone()
    return int(row[0])


def recent(category: str, limit: int = 20) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, entry, to_char(ts,'YYYY-MM-DD HH24:MI') FROM tracker_entries "
            "WHERE category=%s ORDER BY id DESC LIMIT %s", (category, limit)).fetchall()
    return [{"id": int(r[0]), "entry": r[1], "ts": r[2]} for r in rows]


__all__ = ["init_schema", "log_entry", "recent", "TrackerError"]
