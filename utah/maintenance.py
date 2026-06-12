"""Maintenance capability — keep Utah's memory healthy.

Ace's nightly_maintenance transitions HERE as a capability behind the brain, not an agent.
It runs :func:`utah.consolidate.consolidate` (promote unpromoted turns into durable facts
via the brain, then decay/archive faded rows) and reports the counts. Brain-extraction
failures, a consolidate crash, and a malformed report shape are all documented to the
failure log — memory hygiene that never fails silently. The consolidate call is
injectable for tests; the boundary contract is never-raises.
"""
from __future__ import annotations

import logging

from utah import failures

log = logging.getLogger("utah.maintenance")

# Failure-log details are bounded so a pathological exception message (a dumped
# query plan, a recursive repr) cannot flood the failures table.
_MAX_DETAIL = 500

# The counts a consolidation report must carry; anything else is a malformed report.
_REPORT_FIELDS = (
    "turns_seen", "facts_promoted", "facts_skipped", "brain_failures", "archived",
)


def _consolidate():
    from utah.consolidate import consolidate

    return consolidate()


def _coerce_counts(rep) -> dict[str, int] | None:
    """The report's counts as ints, or ``None`` when the shape is wrong.

    Floats are coerced (a numeric count is still a count); bools and non-numerics
    are rejected — a report claiming ``turns_seen='lots'`` is an upstream bug and
    must surface as a documented failure, not an AttributeError mid-cron.
    """
    counts: dict[str, int] = {}
    for field in _REPORT_FIELDS:
        value = getattr(rep, field, None)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        counts[field] = int(value)
    return counts


def run(consolidate_fn=None) -> dict:
    """Run consolidation + decay; document failures. Returns the report counts.
    Never raises."""
    consolidate_fn = consolidate_fn or _consolidate
    try:
        rep = consolidate_fn()
    except Exception as exc:  # noqa: BLE001 — memory/brain down mid-pass
        detail = str(exc)[:_MAX_DETAIL]
        failures.record("maintenance", "consolidate_failed", detail)
        log.warning("maintenance consolidate failed: %s", detail)
        return {"ok": False, "error": detail}

    counts = _coerce_counts(rep)
    if counts is None:
        detail = f"malformed consolidation report: {rep!r}"[:_MAX_DETAIL]
        failures.record("maintenance", "malformed_report", detail)
        log.warning("maintenance: %s", detail)
        return {"ok": False, "error": detail}

    if counts["brain_failures"]:
        failures.record("maintenance", "brain_failures",
                        f"{counts['brain_failures']} turns could not be fact-extracted "
                        "(brain unavailable this pass; retried next run)")
    log.info("maintenance: turns=%d promoted=%d skipped=%d brain_failures=%d archived=%d",
             counts["turns_seen"], counts["facts_promoted"], counts["facts_skipped"],
             counts["brain_failures"], counts["archived"])
    return {"ok": True, "turns_seen": counts["turns_seen"],
            "facts_promoted": counts["facts_promoted"],
            "skipped": counts["facts_skipped"],
            "brain_failures": counts["brain_failures"],
            "archived": counts["archived"]}


__all__ = ["run"]
