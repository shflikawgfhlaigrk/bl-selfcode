"""One-time migration: Ace's SQLite knowledge -> Utah's Postgres memory.

Reads ``~/.ace/ace.db`` ``semantic_memory`` and writes each row into Utah memory through
the SAME admission gate the live brain uses (``memory.store``): embed → de-dup → no-fab →
admit. A junk filter drops transient scrape/news/notification telemetry FIRST so Utah's
clean memory is never polluted (Michael: "everything, gated"; the gate dedups but does not
judge value, so the filter is the quality bar). Every store failure is documented to the
failure log. This module lives OUTSIDE ``utah/`` (it imports sqlite3 to read the OLD store)
so the Utah runtime stays provably SQLite-free.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3

from utah import failures, memory
from utah.embed import EmbedError
from utah.memory import AdmissionDenied, MemoryUnavailable, WriteAction

log = logging.getLogger("migrations.ace_knowledge")

ACE_DB = os.path.expanduser("~/.ace/ace.db")

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


def read_ace_rows(path: str = ACE_DB, limit: int | None = None) -> list[tuple[str, str]]:
    """(agent_id, content) for live (non-superseded) semantic_memory rows."""
    con = sqlite3.connect(path)
    try:
        q = ("SELECT agent_id, content FROM semantic_memory "
             "WHERE content IS NOT NULL AND superseded_by IS NULL ORDER BY id")
        if limit:
            q += f" LIMIT {int(limit)}"
        return [(r[0], r[1]) for r in con.execute(q).fetchall()]
    finally:
        con.close()


def migrate(rows, store_fn=None) -> dict:
    """Run rows through the junk filter + admission gate. Returns stats; never raises."""
    if store_fn is None:
        def store_fn(content):
            return memory.store(content, source="fact", confidence=0.5)
    stats = {"read": 0, "junk": 0, "inserted": 0, "deduped": 0, "rejected": 0, "failed": 0}
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
        except AdmissionDenied:
            stats["rejected"] += 1
        except (EmbedError, MemoryUnavailable) as exc:
            stats["failed"] += 1
            failures.record("migration", "store_failed", f"ace_knowledge row: {exc}")
        if stats["read"] % 500 == 0:
            log.info("ace_knowledge migrate: %s", stats)
    return stats


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    rows = read_ace_rows()
    log.info("read %d ace semantic_memory rows", len(rows))
    stats = migrate(rows)
    log.info("ace_knowledge migration complete: %s", stats)
    print(stats)
    memory.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
