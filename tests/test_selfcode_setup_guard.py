"""propose() "never raises" — the PRE-RUN boundary: branch creation or the safety
snapshot blowing up (bad repo path, unreadable safety file) must return an honest
failure dict and document it, never raise into the daemon/autonomy caller."""
from __future__ import annotations

import pytest

from utah import failures, selfcode
from tests.fakes import FakeFailureStore


@pytest.fixture()
def enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "off")  # absent → enabled
    store = FakeFailureStore()
    failures.set_store(store)
    return store


def test_branch_failure_returns_honest_dict_not_a_raise(enabled):
    def bad_branch(slug):
        raise RuntimeError("git exploded creating the branch")

    r = selfcode.propose("task", branch_fn=bad_branch,
                         run_claude=lambda t: pytest.fail("must not code without a branch"),
                         run_tests=lambda: (True, ""), auto_merge=False)
    assert r["applied"] is False and r["branch"] is None
    assert "git exploded" in r["reason"]
    assert any(kind == "setup_failed" for _, _, kind, _ in enabled.rows)  # documented


def test_safety_snapshot_failure_refuses_run(enabled):
    def bad_snapshot():
        raise OSError("safety file unreadable")

    r = selfcode.propose("task", safety_snapshot_fn=bad_snapshot,
                         branch_fn=lambda s: f"selfcode/{s}",
                         run_claude=lambda t: pytest.fail("must not code without a before-image"),
                         run_tests=lambda: (True, ""), auto_merge=False)
    assert r["applied"] is False and "unreadable" in r["reason"]


def test_setup_failure_during_smoke_stays_out_of_failure_feed(enabled, monkeypatch, tmp_path):
    monkeypatch.setattr(selfcode, "SMOKE_LOG", tmp_path / "smoke.jsonl")
    r = selfcode.propose("task", branch_fn=lambda s: (_ for _ in ()).throw(RuntimeError("x")),
                         run_claude=lambda t: None, run_tests=lambda: (True, ""),
                         auto_merge=False, smoke=True)
    assert r["applied"] is False
    assert enabled.rows == []                              # synthetic outcome → SMOKE_LOG only
