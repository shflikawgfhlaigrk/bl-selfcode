"""Architecture probe IPC budgets — honest timeouts for daemon work RPCs under load.

The architecture probe suite (see ``docs/ACE-ARCHITECTURE-PROBES.md``) exercises
liveness RPCs (ms) and pipeline work RPCs (seconds under governor load). A flat
5s client budget false-fails functional handlers — J-045 splits fast liveness from
bounded work tiers instead of tightening daemon handler timeouts globally.
"""
from __future__ import annotations

import os
import time
from typing import Callable

#: Fast path — event-loop liveness (ping/status/memory/publish).
IPC_LIVENESS_TIMEOUT_S = float(os.environ.get("UTAH_ARCH_PROBE_LIVENESS_S", "5"))
#: Default work RPC budget (morning_brief, run_engines, maintenance_run, …).
IPC_WORK_TIMEOUT_S = float(os.environ.get("UTAH_ARCH_PROBE_WORK_S", "30"))
#: Heavy pipeline triggers — governor queue + real pool work (scout/outreach).
IPC_WORK_HEAVY_TIMEOUT_S = float(os.environ.get("UTAH_ARCH_PROBE_WORK_HEAVY_S", "60"))

LIVENESS_PROBES: tuple[tuple[str, object | None], ...] = (
    ("ping", None),
    ("status", None),
    ("memory_stats", None),
    ("ledger_snapshot", {"limit": 8}),
    ("watchdog_check", None),
    ("panel_detail", {"panel": "spine"}),
    ("publish", {"channel": "probe", "event": {"probe": True}}),
)

WORK_PROBES: tuple[tuple[str, object | None], ...] = (
    ("scout_leads", {"dry_run": True}),
    ("scout_probate", {"dry_run": True}),
    ("queue_outreach", {}),
    ("run_engines", {"dry_run": True}),
    ("morning_brief", {"dry_run": True}),
    ("maintenance_run", {"dry_run": True}),
)

HEAVY_WORK_METHODS = frozenset({"scout_leads", "scout_probate", "queue_outreach"})


def ipc_probe_timeout(method: str) -> float:
    """Return the architecture-probe client budget for *method*."""
    if method in HEAVY_WORK_METHODS:
        return IPC_WORK_HEAVY_TIMEOUT_S
    if any(method == m for m, _ in WORK_PROBES):
        return IPC_WORK_TIMEOUT_S
    return IPC_LIVENESS_TIMEOUT_S


def probe_ipc(
    method: str,
    params: object | None,
    *,
    call_fn: Callable[..., object] | None = None,
    timeout: float | None = None,
) -> dict:
    """One IPC probe — never raises; returns ``{ok, method, timeout_s, latency_ms, error?}``."""
    budget = ipc_probe_timeout(method) if timeout is None else timeout
    t0 = time.monotonic()
    try:
        if call_fn is None:
            from utah.daemon import client as ctl

            call_fn = ctl.call_sync
        call_fn(method, params, timeout=budget)
    except Exception as exc:  # noqa: BLE001 — probe boundary: TimeoutError is a red probe
        return {
            "ok": False,
            "method": method,
            "timeout_s": budget,
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "ok": True,
        "method": method,
        "timeout_s": budget,
        "latency_ms": int((time.monotonic() - t0) * 1000),
    }


def run_ipc_layer(*, call_fn: Callable[..., object] | None = None) -> dict:
    """Run layer-2 IPC probes (liveness + work). Never raises."""
    probes = list(LIVENESS_PROBES) + list(WORK_PROBES)
    results = [probe_ipc(m, p, call_fn=call_fn) for m, p in probes]
    failed = [r["method"] for r in results if not r["ok"]]
    return {
        "ok": not failed,
        "passed": len(results) - len(failed),
        "total": len(results),
        "failed": failed,
        "results": results,
    }


__all__ = [
    "HEAVY_WORK_METHODS",
    "IPC_LIVENESS_TIMEOUT_S",
    "IPC_WORK_HEAVY_TIMEOUT_S",
    "IPC_WORK_TIMEOUT_S",
    "LIVENESS_PROBES",
    "WORK_PROBES",
    "ipc_probe_timeout",
    "probe_ipc",
    "run_ipc_layer",
]
