"""Maintenance capability — keep Utah's memory healthy.

Ace's nightly_maintenance transitions HERE as a capability behind the brain, not an agent.
It runs :func:`utah.consolidate.consolidate` (promote unpromoted turns into durable facts
via the brain, then decay/archive faded rows) and reports the counts. Brain-extraction
failures and a consolidate crash are documented to the failure log — memory hygiene that
never fails silently. The consolidate call is injectable for tests.
"""
from __future__ import annotations

import logging

from utah import failures

log = logging.getLogger("utah.maintenance")


def _consolidate():
    from utah.consolidate import consolidate

    return consolidate()


def run(consolidate_fn=None) -> dict:
    """Run consolidation + decay; document failures. Returns the report counts.
    Never raises."""
    consolidate_fn = consolidate_fn or _consolidate
    try:
        rep = consolidate_fn()
    except Exception as exc:  # noqa: BLE001 — memory/brain down mid-pass
        failures.record("maintenance", "consolidate_failed", str(exc))
        log.warning("maintenance consolidate failed: %s", exc)
        return {"ok": False, "error": str(exc)}

    if getattr(rep, "brain_failures", 0):
        failures.record("maintenance", "brain_failures",
                        f"{rep.brain_failures} turns could not be fact-extracted "
                        "(brain unavailable this pass; retried next run)")
    log.info("maintenance: turns=%d promoted=%d skipped=%d brain_failures=%d archived=%d",
             rep.turns_seen, rep.facts_promoted, rep.facts_skipped, rep.brain_failures, rep.archived)
    return {"ok": True, "turns_seen": rep.turns_seen, "facts_promoted": rep.facts_promoted,
            "skipped": rep.facts_skipped, "brain_failures": rep.brain_failures,
            "archived": rep.archived}


__all__ = ["run"]
