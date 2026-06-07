"""Tests for the SICA overseer (rung 3): cancel decisions + the supervise loop,
all with injected boundaries (no real subprocess)."""
from __future__ import annotations

from utah import sica_overseer as ov


# ── assess (pure) ────────────────────────────────────────────────────────────
def test_assess_ok_under_limits():
    assert ov.assess(elapsed_s=10, cost_usd=0, stall_count=0).cancel is False


def test_assess_cancels_on_time_limit():
    v = ov.assess(elapsed_s=301, time_limit_s=300)
    assert v.cancel is True and "time limit" in v.reason


def test_assess_cancels_on_cost_limit():
    v = ov.assess(elapsed_s=1, cost_usd=10.0, cost_limit_usd=10.0)
    assert v.cancel is True and "cost limit" in v.reason


def test_assess_cancels_on_stall():
    v = ov.assess(elapsed_s=1, stall_count=4, stall_polls=4)
    assert v.cancel is True and "stall" in v.reason


# ── supervise loop ───────────────────────────────────────────────────────────
def test_supervise_run_completes_on_its_own():
    alive = iter([True, False])          # enters loop once, then the run ends
    cancelled = []
    v = ov.supervise(is_alive=lambda: next(alive), elapsed_fn=lambda: 1.0,
                     cancel_fn=lambda: cancelled.append(1), sleep_fn=lambda s: None)
    assert v.cancel is False and v.reason == "completed" and cancelled == []


def test_supervise_cancels_on_time_limit():
    cancelled = []
    v = ov.supervise(is_alive=lambda: True, elapsed_fn=lambda: 999.0,
                     cancel_fn=lambda: cancelled.append(1), sleep_fn=lambda s: None)
    assert v.cancel is True and "time limit" in v.reason and cancelled == [1]


def test_supervise_cancels_on_stall():
    cancelled = []
    v = ov.supervise(is_alive=lambda: True, elapsed_fn=lambda: 5.0,
                     output_fn=lambda: "no-progress", cancel_fn=lambda: cancelled.append(1),
                     sleep_fn=lambda s: None, stall_polls=2)
    # poll1: out new (stall0); poll2: same (stall1); poll3: same (stall2) -> cancel
    assert v.cancel is True and "stall" in v.reason and cancelled == [1]


def test_supervise_changing_output_does_not_stall():
    seq = iter(["a", "b", "c", "d"])
    alive = iter([True, True, True, True, False])
    v = ov.supervise(is_alive=lambda: next(alive), elapsed_fn=lambda: 1.0,
                     output_fn=lambda: next(seq), cancel_fn=lambda: None,
                     sleep_fn=lambda s: None, stall_polls=2)
    assert v.cancel is False   # output keeps changing -> never stalls, run ends naturally
