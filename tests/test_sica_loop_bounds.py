"""MetaLoop bounds hardening: cost-budget stop, exception containment, and
malformed-proposer tolerance.

The loop is the spine of the autonomous lane (`sica_autonomy.run_cycle` builds
one per cycle): a single throwing/garbage proposer attempt must be RECORDED as
a failed attempt — never crash the cycle before its telemetry is logged — and
every budget bound must actually stop the loop.
"""
from __future__ import annotations

from utah import sica
from utah.sica_loop import MetaLoop


def _costly_propose(cost):
    return lambda task: {"utility": 0.5, "tests_passed": True, "merged": False,
                         "cost_usd": cost}


def test_loop_stops_on_cost_budget(tmp_path):
    arch = sica.Archive(tmp_path / "a.jsonl")
    loop = MetaLoop(archive=arch, max_steps=10, cost_budget_usd=10.0,
                    propose_fn=_costly_propose(6.0))
    r = loop.run(["t1", "t2", "t3", "t4"])
    # step1 spends 6 (<10 → continue), step2 spends 12 (≥10 → stop before t3)
    assert r.steps == 2
    assert r.stopped == "cost"


def test_zero_cost_budget_stops_before_any_step(tmp_path):
    arch = sica.Archive(tmp_path / "a.jsonl")
    loop = MetaLoop(archive=arch, cost_budget_usd=0.0,
                    propose_fn=_costly_propose(1.0))
    r = loop.run(["t1"])
    assert r.steps == 0 and r.stopped == "cost"


def test_propose_exception_is_contained_and_recorded(tmp_path):
    """A throwing proposer becomes a failed attempt with the error captured;
    the loop CONTINUES to the next task instead of crashing the cycle."""
    arch = sica.Archive(tmp_path / "a.jsonl")
    calls = []

    def flaky(task):
        calls.append(task)
        if task == "boom":
            raise RuntimeError("coding agent crashed")
        return {"utility": 0.4, "tests_passed": True, "merged": False, "cost_usd": 0.0}

    loop = MetaLoop(archive=arch, max_steps=10, propose_fn=flaky)
    r = loop.run(["boom", "ok"])
    assert calls == ["boom", "ok"]                  # the crash did not end the loop
    assert r.steps == 2
    assert r.attempts[0]["passed"] is False
    assert "coding agent crashed" in r.attempts[0]["error"]
    assert r.attempts[1]["passed"] is True and "error" not in r.attempts[1]


def test_propose_returning_none_is_a_safe_zero_attempt(tmp_path):
    arch = sica.Archive(tmp_path / "a.jsonl")
    loop = MetaLoop(archive=arch, propose_fn=lambda task: None)
    r = loop.run(["t1"])
    assert r.steps == 1
    a = r.attempts[0]
    assert a["utility"] == 0.0 and a["passed"] is False and a["merged"] is False


def test_non_numeric_cost_and_utility_do_not_crash_the_loop(tmp_path):
    """A malformed proposer dict (utility/cost not numbers) degrades to 0.0 —
    a garbage attempt must not take the budget accounting down with it."""
    arch = sica.Archive(tmp_path / "a.jsonl")
    loop = MetaLoop(archive=arch, propose_fn=lambda task: {
        "utility": "not-a-number", "cost_usd": object(), "tests_passed": True})
    r = loop.run(["t1"])
    assert r.steps == 1
    assert r.attempts[0]["utility"] == 0.0


def test_negative_max_steps_is_clamped_to_zero(tmp_path):
    arch = sica.Archive(tmp_path / "a.jsonl")
    loop = MetaLoop(archive=arch, max_steps=-3,
                    propose_fn=lambda task: {"utility": 1.0})
    r = loop.run(["t1"])
    assert r.steps == 0 and r.stopped == "max_steps"
