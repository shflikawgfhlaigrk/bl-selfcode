"""Tests for the SICA autonomy cycle (rung 4) — kill-switch, meta-task selection,
and the governed run — all injected (no real git/claude/brain)."""
from __future__ import annotations

import subprocess

from utah import selfcode, sica, sica_autonomy


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)


def _init_live_and_clone(tmp_path):
    """A real live repo (1 commit) + a clone of it, both with a git identity."""
    live, clone = tmp_path / "live", tmp_path / "clone"
    live.mkdir()
    _git(live, "init", "-q", "-b", "main")
    _git(live, "config", "user.email", "t@t"); _git(live, "config", "user.name", "t")
    (live / "f.txt").write_text("1")
    _git(live, "add", "-A"); _git(live, "commit", "-qm", "base")
    subprocess.run(["git", "clone", "-q", str(live), str(clone)], capture_output=True)
    _git(clone, "config", "user.email", "t@t"); _git(clone, "config", "user.name", "t")
    return live, clone


def _autonomous_commit(clone):
    (clone / "auto.txt").write_text("auto")
    _git(clone, "add", "-A"); _git(clone, "commit", "-qm", "selfcode(auto): x")


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


# ── propagation (clone → live) ───────────────────────────────────────────────
def test_propagate_fast_forwards_clean_live(tmp_path):
    live, clone = _init_live_and_clone(tmp_path)
    _autonomous_commit(clone)
    r = sica_autonomy.propagate(live=live, clone=clone)
    assert r["propagated"] is True
    assert (live / "auto.txt").exists()   # autonomous work now in the live repo


def test_propagate_skips_dirty_live(tmp_path):
    live, clone = _init_live_and_clone(tmp_path)
    _autonomous_commit(clone)
    (live / "uncommitted.txt").write_text("dev WIP")   # live tree dirty
    r = sica_autonomy.propagate(live=live, clone=clone)
    assert r["propagated"] is False and "dirty" in r["reason"]
    assert not (live / "auto.txt").exists()            # dev work untouched, nothing applied


def test_propagate_skips_non_fast_forward(tmp_path):
    live, clone = _init_live_and_clone(tmp_path)
    _autonomous_commit(clone)
    # live diverges with its own committed change → not a fast-forward
    (live / "f.txt").write_text("2")
    _git(live, "add", "-A"); _git(live, "commit", "-qm", "dev")
    r = sica_autonomy.propagate(live=live, clone=clone)
    assert r["propagated"] is False and "fast-forward" in r["reason"]


def test_run_cycle_propagates_on_merge(monkeypatch, tmp_path):
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    called = {}
    r = sica_autonomy.run_cycle(
        repo=tmp_path, sync_fn=lambda repo: True, task_fn=lambda: "do x",
        propose_fn=lambda t: {"utility": 0.9, "tests_passed": True, "merged": True, "cost_usd": 0},
        propagate_fn=lambda clone=None: called.update(ok=True) or {"propagated": True, "to": "abc"})
    assert called.get("ok") is True
    assert r["propagation"]["propagated"] is True


def test_run_cycle_no_propagate_when_not_merged(monkeypatch, tmp_path):
    monkeypatch.setattr(selfcode, "KILL_SWITCH", tmp_path / "nope")
    called = {}
    r = sica_autonomy.run_cycle(
        repo=tmp_path, sync_fn=lambda repo: True, task_fn=lambda: "do x",
        propose_fn=lambda t: {"utility": 0.5, "tests_passed": False, "merged": False, "cost_usd": 0},
        propagate_fn=lambda clone=None: called.update(ok=True) or {})
    assert "ok" not in called and "propagation" not in r
