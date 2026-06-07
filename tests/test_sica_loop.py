"""Tests for the SICA meta-agent outer loop (rung 2): bounded iteration, utility
trajectory, and meta-task generation — all with injected proposer + clock (no
real coding agent runs)."""
from __future__ import annotations

from utah import sica
from utah.sica_loop import MetaLoop


def _recording_propose(archive, utility=0.9, passed=True, merged=False):
    """A fake proposer that archives a scored attempt (like propose_governed would)."""
    def fn(task):
        e = sica.make_attempt(task=task, branch="b", tier="A", passed=passed,
                              output=("2 passed" if passed else "1 failed, 1 passed"),
                              cost_usd=0.0, elapsed_s=1.0, timed_out=False,
                              merged=merged, sha=None, reason="ok")
        d = archive.record(e)
        return {"utility": d["utility"], "tests_passed": passed,
                "merged": merged, "cost_usd": 0.0}
    return fn


def test_loop_runs_each_task_and_archives(tmp_path):
    arch = sica.Archive(tmp_path / "a.jsonl")
    loop = MetaLoop(archive=arch, max_steps=10, propose_fn=_recording_propose(arch))
    r = loop.run(["t1", "t2", "t3"])
    assert r.steps == 3 and len(r.attempts) == 3
    assert arch.count() == 3
    assert r.stopped == "tasks"
    assert [a["task"] for a in r.attempts] == ["t1", "t2", "t3"]


def test_loop_respects_max_steps(tmp_path):
    arch = sica.Archive(tmp_path / "a.jsonl")
    loop = MetaLoop(archive=arch, max_steps=2, propose_fn=_recording_propose(arch))
    r = loop.run(["t1", "t2", "t3", "t4"])
    assert r.steps == 2 and r.stopped == "max_steps"


def test_loop_respects_deadline(tmp_path):
    arch = sica.Archive(tmp_path / "a.jsonl")
    # clock: start=0, then 50, 100, ... ; deadline 60 => first iter (50<60) runs, second (100>=60) stops
    seq = iter([0, 50, 100, 150, 200])
    loop = MetaLoop(archive=arch, max_steps=10, deadline_s=60,
                    propose_fn=_recording_propose(arch), clock=lambda: next(seq))
    r = loop.run(["t1", "t2", "t3"])
    assert r.steps == 1 and r.stopped == "deadline"


def test_loop_tracks_best_and_improved(tmp_path):
    arch = sica.Archive(tmp_path / "a.jsonl")
    # seed a weak attempt so best_before > 0 but improvable
    arch.record(sica.make_attempt(task="seed", branch="b", tier="A", passed=False,
                                  output="3 failed, 1 passed", cost_usd=0, elapsed_s=200,
                                  timed_out=False, merged=False, sha=None, reason="weak"))
    before = arch.best()["utility"]
    loop = MetaLoop(archive=arch, max_steps=5, propose_fn=_recording_propose(arch))
    r = loop.run(["good"])
    assert r.best_before == before
    assert r.best_after >= r.best_before and r.improved is True


def test_next_task_from_archive_uses_brain(tmp_path):
    arch = sica.Archive(tmp_path / "a.jsonl")
    arch.record(sica.make_attempt(task="add weather cache", branch="b", tier="A",
                                  passed=True, output="9 passed", cost_usd=0, elapsed_s=2,
                                  timed_out=False, merged=True, sha="s", reason="green"))
    seen = {}
    def brain(prompt):
        seen["prompt"] = prompt
        return "  add a docstring to leads.py  "
    loop = MetaLoop(archive=arch)
    task = loop.next_task_from_archive(brain)
    assert task == "add a docstring to leads.py"          # stripped
    assert "add weather cache" in seen["prompt"]          # archive context fed to the brain
    assert "never touch safety files" in seen["prompt"]   # the low-risk guardrail


def test_loop_empty_tasks_is_noop(tmp_path):
    arch = sica.Archive(tmp_path / "a.jsonl")
    loop = MetaLoop(archive=arch, propose_fn=_recording_propose(arch))
    r = loop.run([])
    assert r.steps == 0 and r.attempts == [] and r.improved is False
