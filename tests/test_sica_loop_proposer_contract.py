"""MetaLoop proposer-contract hardening (beyond test_sica_loop_bounds).

The loop accepts ANY injected proposer (sica_autonomy wires propose_governed,
tests wire fakes). A proposer that returns a non-dict, raises mid-budget, or
reports garbage numbers must degrade to an honestly-recorded failed/zero
attempt — the cycle's telemetry (and its budget accounting) must survive.
"""
from __future__ import annotations

from utah import sica
from utah.sica_loop import MetaLoop


def test_proposer_returning_non_dict_is_a_recorded_zero_attempt(tmp_path):
    """A proposer that hands back a string/list (malformed contract) becomes a
    failed attempt that NAMES the contract breach — never a TypeError."""
    arch = sica.Archive(tmp_path / "a.jsonl")
    loop = MetaLoop(archive=arch, propose_fn=lambda task: "merged fine, trust me")
    r = loop.run(["t1"])
    assert r.steps == 1
    a = r.attempts[0]
    assert a["utility"] == 0.0 and a["passed"] is False and a["merged"] is False
    assert "dict" in a["error"]          # the breach is named, not silently zeroed


def test_throwing_proposer_does_not_corrupt_budget_accounting(tmp_path):
    """A crash on step 1 must not stop the cost budget from bounding step 2+."""
    arch = sica.Archive(tmp_path / "a.jsonl")
    calls = []

    def proposer(task):
        calls.append(task)
        if task == "boom":
            raise OSError("agent socket closed")
        return {"utility": 0.2, "tests_passed": True, "cost_usd": 6.0}

    loop = MetaLoop(archive=arch, max_steps=10, cost_budget_usd=10.0,
                    propose_fn=proposer)
    r = loop.run(["boom", "ok-1", "ok-2", "ok-3"])
    # boom spends 0 (crash), ok-1 spends 6 (<10 -> continue), ok-2 spends 12 (>=10 -> stop)
    assert calls == ["boom", "ok-1", "ok-2"]
    assert r.stopped == "cost"
    assert r.attempts[0]["passed"] is False and "agent socket closed" in r.attempts[0]["error"]


def test_error_attempt_never_reports_merged_or_passed(tmp_path):
    """Even if a throwing proposer somehow left stale truthy fields around, an
    errored attempt must NEVER claim passed/merged (dishonest-signal guard)."""
    arch = sica.Archive(tmp_path / "a.jsonl")

    def liar(task):
        raise RuntimeError("crashed after claiming success")

    loop = MetaLoop(archive=arch, propose_fn=liar)
    r = loop.run(["t"])
    a = r.attempts[0]
    assert a["passed"] is False and a["merged"] is False and a["utility"] == 0.0


def test_loop_result_shape_is_stable_for_run_cycle(tmp_path):
    """sica_autonomy.run_cycle serializes steps/attempts/best_after — pin the shape."""
    arch = sica.Archive(tmp_path / "a.jsonl")
    loop = MetaLoop(archive=arch, propose_fn=lambda t: {
        "utility": 0.5, "tests_passed": True, "merged": False, "cost_usd": 0.0})
    r = loop.run(["t"])
    assert set(r.attempts[0]) == {"task", "utility", "passed", "merged"}
    assert isinstance(r.best_after, float) and isinstance(r.steps, int)
    assert r.stopped == "tasks"
