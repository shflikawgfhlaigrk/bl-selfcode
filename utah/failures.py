"""Failure log + silent-failure sink — loud, durable observability.

The hard rule: **recording a failure must never cause one.** ``record`` and
``record_silent`` swallow every error (an except-pass site that de-silences
itself must not blow up the caller). Failures persist in Postgres (same cluster
as memory) so they survive restarts; ``recent`` feeds the deck's AUDIT LEDGER
panel (most-recent-first). Real-or-empty — nothing fabricated.
"""
from __future__ import annotations

import logging
import threading
from typing import NamedTuple, Protocol, Sequence

from utah import config

log = logging.getLogger("utah.failures")

MAX_DETAIL = 2000

#: Upper bound for ``recent`` — a deck/RPC bug asking for 10**9 rows must not
#: turn into a full-table scan on the live failures table.
MAX_RECENT = 1000


class FailureRow(NamedTuple):
    source: str
    kind: str
    detail: str
    ts: str = ""


class FailureStore(Protocol):
    def init_schema(self) -> None: ...
    def insert(self, source: str, kind: str, detail: str) -> None: ...
    def recent(self, limit: int) -> list[FailureRow]: ...
    def count(self) -> int: ...
    def close(self) -> None: ...


_DDL = """
CREATE TABLE IF NOT EXISTS failures (
  id     bigserial PRIMARY KEY,
  ts     timestamptz NOT NULL DEFAULT now(),
  source text NOT NULL,
  kind   text NOT NULL,
  detail text NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS failures_id_desc ON failures (id DESC);
"""


class PostgresFailureStore:
    """Durable append-only failure log on Postgres, over the shared bounded pool.

    Writes can come from any worker thread, so a pooled connection (rather than one
    connection behind an RLock) lets concurrent failure records run concurrently and
    recycles a dropped connection transparently (B6/B10)."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn if dsn is not None else config.DB_DSN

    def _pool(self):
        from utah import db_pool

        return db_pool.get_pool(self._dsn)   # plain connections; no pgvector needed

    def init_schema(self) -> None:
        with self._pool().connection() as conn:
            conn.execute(_DDL)

    def insert(self, source: str, kind: str, detail: str) -> None:
        with self._pool().connection() as conn:
            conn.execute(
                "INSERT INTO failures (source, kind, detail) VALUES (%s, %s, %s)",
                (source, kind, detail),
            )

    def recent(self, limit: int) -> list[FailureRow]:
        with self._pool().connection() as conn:
            rows = conn.execute(
                "SELECT source, kind, detail, to_char(ts, 'HH24:MI:SS') "
                "FROM failures ORDER BY id DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return [FailureRow(r[0], r[1], r[2], r[3]) for r in rows]

    def count(self) -> int:
        with self._pool().connection() as conn:
            return int(conn.execute("SELECT count(*) FROM failures").fetchone()[0])

    def close(self) -> None:
        from utah import db_pool

        db_pool.close_pool(self._dsn)


_store: FailureStore | None = None
_lock = threading.Lock()


def get_store() -> FailureStore:
    """Process-global failure store; created (and schema ensured) lazily."""
    global _store
    with _lock:
        if _store is None:
            store = PostgresFailureStore()
            store.init_schema()
            _store = store
        return _store


def set_store(store: FailureStore | None) -> None:
    """Inject a store (tests). ``None`` restores the lazy Postgres store."""
    global _store
    with _lock:
        _store = store


def record(source: str, kind: str, detail: str = "") -> None:
    """Record a failure. NEVER raises — de-silencing must not cause a failure.

    Genuinely-critical kinds (``config.CRITICAL_FAILURE_KINDS``) ALSO page the phone,
    fired on a background thread so this hot path never blocks, deduped so a storm
    (e.g. a Postgres restart) pages once, not forty times. Paging must never cause a
    failure either, so the whole hook is swallowed."""
    try:
        get_store().insert(str(source), str(kind), str(detail)[:MAX_DETAIL])
    except Exception:  # noqa: BLE001 — the whole point is to never propagate
        log.debug("failure-record swallowed (source=%s kind=%s)", source, kind, exc_info=True)
    try:
        if str(kind) in config.CRITICAL_FAILURE_KINDS:
            from utah import alerts
            alerts.critical_async(str(source), str(detail), key=f"{source}/{kind}")
    except Exception:  # noqa: BLE001 — paging must never break recording
        log.debug("failure-page swallowed (source=%s kind=%s)", source, kind, exc_info=True)
    try:
        from utah.integrations import discord_feed

        discord_feed.feed_audit(str(source), str(kind), str(detail))
    except Exception:  # noqa: BLE001 — Discord must never break recording
        log.debug("failure-discord swallowed (source=%s kind=%s)", source, kind, exc_info=True)


def record_silent(source: str, detail: str = "") -> None:
    """De-silence sink for ``except: pass`` sites. NEVER raises."""
    record(source, "silent", detail)


def recent(limit: int = 20) -> list[FailureRow]:
    """Most-recent-first failures for the deck. Empty (never fabricated) on error.

    *limit* is clamped to [1, MAX_RECENT]: a zero/negative ask still returns one
    row (not an error), and an absurd ask never becomes a full-table scan."""
    try:
        bounded = max(1, min(int(limit), MAX_RECENT))
    except (TypeError, ValueError):
        bounded = 20  # garbage limit → the deck's default page size
    try:
        return get_store().recent(bounded)
    except Exception:  # noqa: BLE001
        log.debug("failure-recent swallowed", exc_info=True)
        return []


def count() -> int:
    try:
        return get_store().count()
    except Exception:  # noqa: BLE001
        return 0


__all__ = [
    "FailureRow", "FailureStore", "PostgresFailureStore",
    "count", "get_store", "record", "record_silent", "recent", "set_store",
]
