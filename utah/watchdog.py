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
import os

from utah import failures

log = logging.getLogger("utah.watchdog")

#: Per-core load that counts as a genuine anomaly; matches the governor's
#: max_load_per_core. Env-tunable so the bar moves WITH the governor's, not after a deploy.
LOAD_CRITICAL = float(os.environ.get("UTAH_WATCHDOG_LOAD_CRITICAL", "1.5"))


def _live_status():
    from utah.daemon import client as ctl

    try:
        return ctl.call_sync("status", timeout=5.0)
    except Exception:  # noqa: BLE001 — unreachable is the signal, not an error to raise
        return None


def _live_failcount() -> int:
    store = failures.get_store()
    return store.count() if hasattr(store, "count") else 0


def _load_per_core(status: dict) -> float | None:
    """The governor's per-core load out of a status payload, or None when absent
    or junk-typed (a malformed daemon reply must read as 'unknown', not crash)."""
    gov = status.get("governor")
    raw = gov.get("load_per_core") if isinstance(gov, dict) else None
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        log.warning("watchdog: non-numeric load_per_core in status: %r", raw)
        return None


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

    if not isinstance(status, dict):
        # None OR garbage: a daemon that answers a non-dict is not answering.
        # (.get() on a str/list here used to crash the watchdog itself.)
        failures.record("watchdog", "daemon_unreachable",
                        "control ping failed — daemon not responding to status")
        return {"healthy": False, "daemon_up": False, "load_per_core": None,
                "failures": None, "anomalies": ["daemon_unreachable"]}

    anomalies: list[str] = []
    load = _load_per_core(status)
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
