"""One-time migration: Ace's SQLite knowledge -> Utah's Postgres memory.

Reads ``~/.ace/ace.db`` ``semantic_memory`` and writes each row into Utah memory through
the SAME admission gate the live brain uses (``memory.store``): embed → de-dup → no-fab →
admit. A junk filter drops transient scrape/news/notification telemetry FIRST so Utah's
clean memory is never polluted (Michael: "everything, gated"; the gate dedups but does not
judge value, so the filter is the quality bar). Every store failure is documented to the
failure log. This module lives OUTSIDE ``utah/`` (it imports sqlite3 to read the OLD store)
so the Utah runtime stays provably SQLite-free.

Boundedness + atomicity (hardening pass, 2026-06-12):

* sqlite connects are **read-only** (``mode=ro`` — a plain connect silently CREATED an
  empty ace.db on a missing path) and carry an explicit lock **timeout**
  (``UTAH_MIGRATION_DB_TIMEOUT``, default 10s). The Postgres side is already bounded:
  every ``memory.store`` checkout rides :mod:`utah.db_pool`
  (``connect_timeout=DB_CONNECT_TIMEOUT``).
* Writes are **compensated in batches**. Each ``memory.store`` insert commits its own
  transaction inside the gate, so the migration cannot wrap N gate calls in one BEGIN.
  Instead it tracks the ids inserted in the open batch (up to ``batch_size``) and, if the
  store goes DOWN mid-batch (:class:`MemoryUnavailable`), deletes those rows — entity
  links and supersede marks included — in ONE pooled transaction, then aborts honestly.
  Net effect: the durable result is always whole batches; a re-run resumes cleanly
  because the dedup gate turns already-migrated rows into REINFORCED (idempotent — the
  production run of 9,179 facts on 2026-06-06 stays safe to repeat). Reinforcement bumps
  on rows deduped during a failed batch are NOT compensated; a +1 frequency signal is
  harmless and re-running converges.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from typing import Callable, Iterable, Sequence

from utah import failures, memory
from utah.embed import EmbedError
from utah.memory import AdmissionDenied, MemoryUnavailable, WriteAction

log = logging.getLogger("migrations.ace_knowledge")

ACE_DB = os.environ.get("UTAH_ACE_DB", os.path.expanduser("~/.ace/ace.db"))

#: Lock-wait bound for the sqlite read side (seconds). Postgres writes are bounded by
#: the shared pool (connect_timeout + checkout timeout) inside ``memory.store``.
DB_TIMEOUT_S = float(os.environ.get("UTAH_MIGRATION_DB_TIMEOUT", "10"))

#: Compensation window: at most this many NEWLY-INSERTED rows are un-rolled-back-able
#: if the store dies mid-run (a full batch is durable by design).
DEFAULT_BATCH_SIZE = 500

#: Content/agent markers that are transient telemetry, not durable knowledge.
_JUNK_MARKERS = re.compile(
    r"\[TRUMP_TRUTH\]|\[No Title\]|Post from\s+\w+.*\d{4}|^\s*RT @|^\s*https?://\S+\s*$",
    re.I,
)
_JUNK_AGENTS = frozenset({"notifier", "timer", "idea_harvester", "news"})
_MIN_LEN = 25  # shorter than this can't be a useful atomic fact


def is_junk(content: str, agent_id: str) -> bool:
    if (agent_id or "").lower() in _JUNK_AGENTS:
        return True
    c = (content or "").strip()
    if len(c) < _MIN_LEN:
        return True
    if _JUNK_MARKERS.search(c):
        return True
    return False


def _connect_ro(path: str, timeout: float) -> sqlite3.Connection:
    """Read-only sqlite connect with a real lock timeout. ``mode=ro`` makes a missing
    ace.db fail honestly (OperationalError) instead of creating an empty database file
    that then fails later with a misleading 'no such table'."""
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout)


def read_ace_rows(
    path: str = ACE_DB,
    limit: int | None = None,
    *,
    timeout: float = DB_TIMEOUT_S,
    connect_fn: Callable[[str, float], sqlite3.Connection] | None = None,
) -> list[tuple[str, str]]:
    """(agent_id, content) for live (non-superseded) semantic_memory rows.

    Raises ``sqlite3.Error`` on a missing/locked/corrupt source db — the CLI boundary
    (:func:`main`) catches it; callers embedding this in a pipeline should too.
    """
    con = (connect_fn or _connect_ro)(path, timeout)
    try:
        q = ("SELECT agent_id, content FROM semantic_memory "
             "WHERE content IS NOT NULL AND superseded_by IS NULL ORDER BY id")
        if limit:
            q += f" LIMIT {int(limit)}"
        return [(r[0], r[1]) for r in con.execute(q).fetchall()]
    finally:
        con.close()


def rollback_inserted(mem_ids: Sequence[int]) -> dict:
    """Compensating delete for a partially-applied batch — never raises.

    In ONE transaction on a pooled connection (bounded: ``connect_timeout`` +
    checkout timeout, same pool as :mod:`utah.product.tasks`):

    1. un-supersede/un-archive anything the doomed rows superseded (the gate only ever
       supersedes live rows, so restoring both flags is exact),
    2. drop the rows' entity links (``mem_entity`` has no FK cascade),
    3. delete the rows themselves.

    Returns ``{"ok": bool, "deleted": int, "restored": int, "error": str | None}``.
    """
    ids = [int(i) for i in mem_ids]
    if not ids:
        return {"ok": True, "deleted": 0, "restored": 0, "error": None}
    import psycopg

    from utah import config, db_pool

    try:
        with db_pool.get_pool(config.DB_DSN).connection() as conn:
            with conn.transaction():
                # SET LOCAL: statement bound for THIS destructive batch only — dies
                # with the transaction, never leaks onto the shared pool connection.
                conn.execute(
                    f"SET LOCAL statement_timeout = {int(config.DB_STATEMENT_TIMEOUT_MS)}")
                restored = conn.execute(
                    "UPDATE memory SET superseded_by = NULL, archived = false "
                    "WHERE superseded_by = ANY(%s)",
                    (ids,),
                ).rowcount or 0
                conn.execute("DELETE FROM mem_entity WHERE mem_id = ANY(%s)", (ids,))
                deleted = conn.execute(
                    "DELETE FROM memory WHERE id = ANY(%s)", (ids,)
                ).rowcount or 0
        return {"ok": True, "deleted": deleted, "restored": restored, "error": None}
    except psycopg.Error as exc:
        return {"ok": False, "deleted": 0, "restored": 0, "error": str(exc)}


def migrate(
    rows: Iterable[tuple[str, str]],
    store_fn: Callable | None = None,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    rollback_fn: Callable[[Sequence[int]], dict] | None = None,
) -> dict:
    """Run rows through the junk filter + admission gate. Returns stats; never raises.

    Per-row outcomes: junk (filtered), inserted, deduped (gate REINFORCED — what makes a
    re-run idempotent), rejected (:class:`AdmissionDenied` policy), failed
    (:class:`EmbedError` — documented, migration continues; the store is still up).

    :class:`MemoryUnavailable` means the store is DOWN: continuing would burn through
    every remaining row, and the open batch is half-applied. The migration compensates —
    ``rollback_fn`` (default :func:`rollback_inserted`) deletes the open batch's inserts —
    then ABORTS with ``ok=False, aborted=True``. ``rollback_ok=False`` flags rows left
    behind (they remain counted in ``inserted`` — honest accounting).
    """
    if store_fn is None:
        def store_fn(content):
            return memory.store(content, source="fact", confidence=0.5)
    if rollback_fn is None:
        rollback_fn = rollback_inserted
    stats = {
        "read": 0, "junk": 0, "inserted": 0, "deduped": 0, "rejected": 0, "failed": 0,
        "rolled_back": 0, "rollback_ok": True, "aborted": False, "ok": True,
    }
    batch_ids: list[int] = []  # inserts in the OPEN batch (compensation window)
    for agent_id, content in rows:
        stats["read"] += 1
        if is_junk(content, agent_id):
            stats["junk"] += 1
            continue
        try:
            res = store_fn(content)
            if getattr(res, "action", None) == WriteAction.REINFORCED:
                stats["deduped"] += 1
            else:
                stats["inserted"] += 1
                rid = getattr(res, "id", None)
                if rid is not None:
                    batch_ids.append(int(rid))
        except AdmissionDenied:
            stats["rejected"] += 1
        except EmbedError as exc:
            stats["failed"] += 1
            failures.record("migration", "store_failed", f"ace_knowledge row: {exc}")
        except MemoryUnavailable as exc:
            stats["failed"] += 1
            failures.record("migration", "store_down", f"ace_knowledge abort: {exc}")
            _compensate(stats, batch_ids, rollback_fn)
            stats["aborted"] = True
            stats["ok"] = False
            log.error("ace_knowledge migrate ABORTED (store down): %s", stats)
            return stats
        if len(batch_ids) >= batch_size:
            batch_ids = []  # batch full -> durable; nothing here rolls back any more
        if stats["read"] % 500 == 0:
            log.info("ace_knowledge migrate: %s", stats)
    return stats


def _compensate(stats: dict, batch_ids: list[int],
                rollback_fn: Callable[[Sequence[int]], dict]) -> None:
    """Roll back the open batch, updating stats honestly. Containment boundary: a
    blowing-up injected/real rollback must not break migrate's never-raises contract."""
    if not batch_ids:
        return
    try:
        rb = rollback_fn(batch_ids)
    except Exception as exc:  # noqa: BLE001 — containment: report, never propagate
        rb = {"ok": False, "deleted": 0, "restored": 0, "error": f"rollback raised: {exc}"}
    if rb.get("ok"):
        stats["rolled_back"] += len(batch_ids)
        stats["inserted"] -= len(batch_ids)  # those rows are gone again
        log.info("ace_knowledge rolled back open batch: %s", rb)
    else:
        stats["rollback_ok"] = False
        failures.record("migration", "rollback_failed",
                        f"ace_knowledge: {len(batch_ids)} rows left behind: {rb.get('error')}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        rows = read_ace_rows()
    except sqlite3.Error as exc:
        log.error("cannot read ace.db at %s: %s", ACE_DB, exc)
        return 1
    log.info("read %d ace semantic_memory rows", len(rows))
    try:
        stats = migrate(rows)
        log.info("ace_knowledge migration complete: %s", stats)
        print(stats)
        return 0 if stats["ok"] else 1
    finally:
        memory.close()


if __name__ == "__main__":
    raise SystemExit(main())
