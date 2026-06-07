"""Watchdog capability — a health check on Utah's own live state.

Ace's watchdog transitions HERE as a capability behind the brain, not an agent. The
supervisor already restarts dead children; this adds an application-level health snapshot
and records GENUINE anomalies (daemon unreachable, governor load critical) to the failure
log, so degradation is never silent — Michael's run-forever rule. It does NOT re-record the
failures already in the log (no noise); it only flags live anomalies it newly observes.

status/failure-count are injectable; detection is pure.
"""
from __future__ import annotations

import logging

from utah import failures

log = logging.getLogger("utah.watchdog")

LOAD_CRITICAL = 8.0  # per-core; matches the governor's max_load_per_core


def _live_status():
    from utah.daemon import client as ctl

    try:
        return ctl.call_sync("status", timeout=5.0)
    except Exception:  # noqa: BLE001 — unreachable is the signal, not an error to raise
        return None


def _live_failcount() -> int:
    store = failures.get_store()
    return store.count() if hasattr(store, "count") else 0


def check(*, status_fn=None, failure_count_fn=None) -> dict:
    """Snapshot Utah's health; record genuine anomalies. Returns
    ``{healthy, daemon_up, load_per_core, failures, anomalies}``. Never raises."""
    status_fn = status_fn or _live_status
    failure_count_fn = failure_count_fn or _live_failcount

    try:
        status = status_fn()
    except Exception as exc:  # noqa: BLE001 — a throwing probe means it's down
        log.warning("watchdog: status probe raised: %s", exc)
        status = None

    if status is None:
        failures.record("watchdog", "daemon_unreachable",
                        "control ping failed — daemon not responding to status")
        return {"healthy": False, "daemon_up": False, "load_per_core": None,
                "failures": None, "anomalies": ["daemon_unreachable"]}

    anomalies: list[str] = []
    load = (status.get("governor") or {}).get("load_per_core")
    if load is not None and load > LOAD_CRITICAL:
        anomalies.append("load_critical")
        failures.record("watchdog", "load_critical",
                        f"governor load_per_core {load} over critical {LOAD_CRITICAL}")

    try:
        failcount = failure_count_fn()
    except Exception:  # noqa: BLE001
        failcount = None

    return {"healthy": not anomalies, "daemon_up": True, "load_per_core": load,
            "failures": failcount, "anomalies": anomalies}


__all__ = ["check", "LOAD_CRITICAL"]
