"""Self-coding capability — Ace's agent_007 transitions here (NOT an agent): propose a
code change on an ISOLATED branch, gated by the test suite. Never main; a kill-switch flag
disables it; a red suite is discarded and the reason documented to the failure log. The
heavy boundaries (Claude CLI, pytest, git) are injectable so the gate logic is proven
without spawning anything."""
from __future__ import annotations

from utah import failures, selfcode
from tests.fakes import FakeFailureStore


def _vcs():
    calls = {"branch": None, "discarded": False}

    def branch_fn(slug):
        calls["branch"] = f"selfcode/{slug}"
        return calls["branch"]

    def discard_fn():
        calls["discarded"] = True

    return calls, branch_fn, discard_fn


def test_kill_switch_blocks_and_is_documented(monkeypatch, tmp_path):
    store = FakeFailureStore(); failures.set_store(store)
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "selfcode.disabled")
    (tmp_path / "selfcode.disabled").write_text("off")
    calls, bf, df = _vcs()
    r = selfcode.propose("add a docstring", run_claude=lambda t: 1 / 0,
                         run_tests=lambda: (True, ""), branch_fn=bf, discard_fn=df)
    assert r["applied"] is False and r["disabled"] is True
    assert calls["branch"] is None                       # never even started
    assert any("disabled" in row[2] for row in store.rows)


def test_green_suite_keeps_change_on_branch(monkeypatch, tmp_path):
    failures.set_store(FakeFailureStore())
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    calls, bf, df = _vcs()
    r = selfcode.propose("add helper", run_claude=lambda t: None,
                         run_tests=lambda: (True, "5 passed"), branch_fn=bf, discard_fn=df)
    assert r["applied"] is True and r["tests_passed"] is True
    assert r["branch"] == "selfcode/add-helper"          # isolated branch, not main
    assert calls["discarded"] is False                   # kept


def test_red_suite_discards_and_documents_why(monkeypatch, tmp_path):
    store = FakeFailureStore(); failures.set_store(store)
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    calls, bf, df = _vcs()
    r = selfcode.propose("break something", run_claude=lambda t: None,
                         run_tests=lambda: (False, "E   assert 1 == 2\n1 failed"),
                         branch_fn=bf, discard_fn=df)
    assert r["applied"] is False and r["tests_passed"] is False
    assert calls["discarded"] is True                     # change rolled back
    assert any("tests_failed" in row[2] for row in store.rows)
    assert any("1 failed" in row[3] for row in store.rows)   # the WHY is recorded


def test_claude_crash_is_documented_and_discarded(monkeypatch, tmp_path):
    store = FakeFailureStore(); failures.set_store(store)
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    calls, bf, df = _vcs()
    def boom(task):
        raise RuntimeError("claude timed out after 300s")
    r = selfcode.propose("x", run_claude=boom, run_tests=lambda: (True, ""),
                         branch_fn=bf, discard_fn=df)
    assert r["applied"] is False
    assert calls["discarded"] is True
    assert any("claude timed out" in row[3] for row in store.rows)
