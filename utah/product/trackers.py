"""Trackers capability — Ace's gym/habits/journal transition here (NOT agents): a real
Postgres-backed timestamped log keyed by category. log(category, entry) + recent(category).
Ungated, no SQLite. One table covers gym/habits/journal/anything.

Every DB touch rides the shared per-DSN pool (:mod:`utah.db_pool`) so it is BOUNDED —
``connect_timeout`` on creation, checkout capped at ``DB_POOL_TIMEOUT`` — and any psycopg
failure surfaces as :class:`TrackerError`, never a raw driver exception into the deck's
trackers panel. Limits are clamped (a negative LIMIT is a Postgres error, not a panel
crash); bad input is rejected before a DB round-trip.
"""
from __future__ import annotations

import contextlib
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

#: Hard cap on one ``recent`` page — the deck panel asks for 30; nothing needs thousands.
MAX_RECENT = 500


class TrackerError(UtahError):
    """Tracker store unreachable or the request was invalid."""


@contextlib.contextmanager
def _conn():
    """An autocommit connection from the shared per-DSN pool — same bounded pattern as
    tasks.py (``connect_timeout=DB_CONNECT_TIMEOUT``, checkout capped at
    ``DB_POOL_TIMEOUT``). A fresh ``psycopg.connect`` per call had NO connect timeout:
    a stalled Postgres hung the trackers panel forever."""
    from utah import db_pool

    try:
        with db_pool.get_pool(config.DB_DSN).connection() as c:
            yield c
    except psycopg.Error as exc:
        raise TrackerError(f"tracker store unreachable: {exc}") from exc


def init_schema() -> None:
    with _conn() as c:
        c.execute(_DDL)


def log_entry(category: str, entry: str) -> int:
    category = (str(category or "").strip() or "journal")[:100]
    entry = str(entry or "").strip()
    if not entry:
        raise TrackerError("empty entry")
    with _conn() as c:
        row = c.execute("INSERT INTO tracker_entries (category, entry) VALUES (%s,%s) RETURNING id",
                        (category, entry)).fetchone()
        entry_id = int(row[0])           # read INSIDE the checkout (pooled conns reset on return)
    return entry_id


def recent(category: str, limit: int = 20) -> list[dict]:
    try:
        limit = max(1, min(int(limit), MAX_RECENT))   # negative LIMIT is a PG error; clamp
    except (TypeError, ValueError):
        limit = 20   # garbage limit (deck query-string drift) -> default page, not a crash
    with _conn() as c:
        rows = c.execute(
            "SELECT id, entry, to_char(ts,'YYYY-MM-DD HH24:MI') FROM tracker_entries "
            "WHERE category=%s ORDER BY id DESC LIMIT %s", (category, limit)).fetchall()
        out = [{"id": int(r[0]), "entry": r[1], "ts": r[2]} for r in rows]
    return out


__all__ = ["init_schema", "log_entry", "recent", "TrackerError", "MAX_RECENT"]
