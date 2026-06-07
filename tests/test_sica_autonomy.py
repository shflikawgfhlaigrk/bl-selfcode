"""Tests for the SICA autonomy cycle (rung 4) — kill-switch, meta-task selection,
and the governed run — all injected (no real git/claude/brain)."""
from __future__ import annotations

from utah import selfcode, sica, sica_autonomy


def test_cycle_respects_kill_switch(monkeypatch, tmp_path):
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "on")
    (tmp_path / "on").write_text("")          # kill switch present
    r = sica_autonomy.run_cycle(repo=tmp_path, brain_fn=lambda p: "x",
                                propose_fn=lambda t: {}, sync_fn=lambda repo: True)
    assert r["ran"] is False and r["reason"] == "kill switch"


def test_cycle_aborts_if_repo_not_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    r = sica_autonomy.run_cycle(repo=tmp_path / "missing", brain_fn=lambda p: "x",
                                propose_fn=lambda t: {})   # default sync_repo → not a git repo
    assert r["ran"] is False and "not ready" in r["reason"]


def test_cycle_runs_meta_task_through_governed(monkeypatch, tmp_path):
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    # archive so the meta-agent has context; capture what the governed proposer got
    arch = sica.Archive(tmp_path / "arch.jsonl")
    monkeypatch.setattr(sica, "ARCHIVE_PATH", tmp_path / "arch.jsonl")
    seen = {}

    def fake_propose(task):
        seen["task"] = task
        e = sica.make_attempt(task=task, branch="b", tier="A", passed=True,
                              output="3 passed", cost_usd=0, elapsed_s=2, timed_out=False,
                              merged=False, sha=None, reason="green")
        arch.record(e)
        return {"utility": e.utility, "tests_passed": True, "merged": False, "cost_usd": 0}

    r = sica_autonomy.run_cycle(repo=tmp_path, sync_fn=lambda repo: True,
                                task_fn=lambda: "add a docstring to leads.py",
                                propose_fn=fake_propose)
    assert r["ran"] is True
    assert r["task"] == "add a docstring to leads.py"
    assert seen["task"] == "add a docstring to leads.py"
    assert r["steps"] == 1 and r["attempts"][0]["utility"] > 0.9


def test_cycle_falls_back_to_default_task_when_brain_silent(monkeypatch, tmp_path):
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    monkeypatch.setattr(sica, "ARCHIVE_PATH", tmp_path / "arch.jsonl")
    seen = {}

    def fake_propose(task):
        seen["task"] = task
        return {"utility": 0.5, "tests_passed": False, "merged": False, "cost_usd": 0}

    r = sica_autonomy.run_cycle(repo=tmp_path, sync_fn=lambda repo: True,
                                task_fn=lambda: "   ",   # task source returns nothing usable
                                propose_fn=fake_propose)
    assert r["task"] == sica_autonomy.DEFAULT_TASK
    assert seen["task"] == sica_autonomy.DEFAULT_TASK
