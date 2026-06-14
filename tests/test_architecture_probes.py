"""Architecture probe IPC budgets — tiered timeouts, injectable call boundary."""
from __future__ import annotations

import pytest

from utah import architecture_probes as ap


def test_liveness_methods_keep_fast_budget():
    for method, _ in ap.LIVENESS_PROBES:
        assert ap.ipc_probe_timeout(method) == ap.IPC_LIVENESS_TIMEOUT_S


def test_heavy_work_methods_use_heavy_budget():
    for method in ap.HEAVY_WORK_METHODS:
        assert ap.ipc_probe_timeout(method) == ap.IPC_WORK_HEAVY_TIMEOUT_S


def test_other_work_methods_use_default_work_budget():
    heavy = ap.HEAVY_WORK_METHODS
    for method, _ in ap.WORK_PROBES:
        if method in heavy:
            continue
        assert ap.ipc_probe_timeout(method) == ap.IPC_WORK_TIMEOUT_S


def test_default_budgets_are_honest_under_load():
    assert ap.IPC_LIVENESS_TIMEOUT_S == 5.0
    assert ap.IPC_WORK_TIMEOUT_S == 30.0
    assert ap.IPC_WORK_HEAVY_TIMEOUT_S == 60.0


def test_probe_ipc_uses_tier_timeout(monkeypatch):
    seen: dict[str, float] = {}

    def fake_call(method, params, *, timeout):
        seen["method"] = method
        seen["timeout"] = timeout
        return {"ok": True}

    r = ap.probe_ipc("scout_leads", {"dry_run": True}, call_fn=fake_call)
    assert r["ok"] is True
    assert seen["timeout"] == ap.IPC_WORK_HEAVY_TIMEOUT_S


def test_probe_ipc_timeout_is_a_red_probe():
    def slow(_method, _params, *, timeout):
        raise TimeoutError(f"timed out after {timeout}s")

    r = ap.probe_ipc("ping", None, call_fn=slow, timeout=0.1)
    assert r["ok"] is False
    assert "TimeoutError" in r["error"]


def test_run_ipc_layer_aggregates_failures():
    def ok_call(method, params, *, timeout):
        if method == "scout_leads":
            raise TimeoutError("still slow")
        return {}

    out = ap.run_ipc_layer(call_fn=ok_call)
    assert out["ok"] is False
    assert out["failed"] == ["scout_leads"]
    assert out["passed"] == out["total"] - 1
