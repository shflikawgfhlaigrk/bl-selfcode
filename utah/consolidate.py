"""Sleep-time consolidation — the compounding engine, off the hot path.

Promotes raw turns into durable atomic facts via the brain's fact extractor,
then runs the decay pass. **There is no side door:** every promoted fact goes
through :func:`utah.memory.store` — the same admission gate, dedup-reinforce,
and supersede pipeline as live writes — and carries ``source='consolidation'``
provenance. That is the structural fix for the Newman class of bug: a fact
written by consolidation supersedes a stale live fact about the same entity
exactly like a live write would.

Failure semantics: if the brain is unavailable for a turn, the turn stays
unmarked and is retried next pass (nothing silently dropped). If a single fact
is rejected by admission or fails to embed, it is counted and skipped; the
pass continues. Only a dead store aborts the pass (it cannot do anything).
"""
from __future__ import annotations

import logging

from utah import brain, config, memory
from utah.embed import EmbedError
from utah.objects import ConsolidationReport

log = logging.getLogger("utah.consolidate")


def consolidate(limit: int = 20) -> ConsolidationReport:
    """Run one consolidation pass over up to *limit* unpromoted turns.

    *limit* is clamped to >= 0 before it reaches the backend: the real store
    runs ``LIMIT %s`` and a negative value is a Postgres error that would
    abort the whole maintenance pass instead of degrading to a no-op.

    Raises:
        MemoryUnavailable: the store is down (the pass cannot run at all).
    """
    backend = memory.get_backend()
    turns = backend.unpromoted_turns(max(0, int(limit)))

    promoted = 0
    skipped = 0
    brain_failures = 0
    for turn_id, content in turns:
        facts = brain.extract_facts(content)
        if facts is None:  # brain down: leave unmarked, retry next pass
            brain_failures += 1
            continue
        for fact in facts[: config.MAX_FACTS_PER_TURN]:
            if not isinstance(fact, str) or not fact.strip():
                # Malformed extractor output (None/int/blank) would crash
                # memory.store with an untyped AttributeError — aborting the
                # pass and stranding the turn forever. Count it and move on.
                skipped += 1
                log.warning("fact skipped (not a usable string): %.80r", fact)
                continue
            try:
                memory.store(fact, source="consolidation", confidence=0.8)
                promoted += 1
            except (memory.AdmissionDenied, EmbedError) as exc:
                skipped += 1
                log.warning("fact skipped (%s): %.80s", exc, fact)
        backend.mark_promoted(turn_id)

    archived = memory.decay()
    report = ConsolidationReport(
        turns_seen=len(turns),
        facts_promoted=promoted,
        facts_skipped=skipped,
        brain_failures=brain_failures,
        archived=archived,
    )
    log.info(
        "consolidation: %d turns, %d promoted, %d skipped, %d brain failures, %d archived",
        report.turns_seen,
        report.facts_promoted,
        report.facts_skipped,
        report.brain_failures,
        report.archived,
    )
    return report
