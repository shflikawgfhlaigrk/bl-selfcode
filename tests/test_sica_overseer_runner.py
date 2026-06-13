"""run_claude_supervised against REAL subprocesses (stub scripts stand in for
the claude CLI) — the Popen runner must honor exit codes, survive a child that
dies before reading its task, actually KILL a runaway run, and bound the final
pipe drain. Plus the limit pass-through that makes the overseer testable.
"""
from __future__ import annotations

import os
import subprocess
import time

import pytest

from utah import config, sica_overseer as ov


def _stub_brain(tmp_path, monkeypatch, body: str):
    """Install a tiny shell script as config.BRAIN_CMD (the runner's only exec)."""
    script = tmp_path / "fake-claude"
    script.write_text("#!/bin/sh\n" + body + "\n")
    script.chmod(0o755)
    monkeypatch.setattr(config, "BRAIN_CMD", str(script))
    return script


def test_clean_exit_returns_none(tmp_path, monkeypatch):
    _stub_brain(tmp_path, monkeypatch, "cat >/dev/null\nexit 0")
    assert ov.run_claude_supervised("do a thing", cwd=str(tmp_path), poll_s=0.05) is None


def test_nonzero_exit_raises_with_stderr_tail(tmp_path, monkeypatch):
    _stub_brain(tmp_path, monkeypatch, 'cat >/dev/null\necho "kaboom detail" >&2\nexit 3')
    with pytest.raises(RuntimeError, match="exited 3") as exc:
        ov.run_claude_supervised("task", cwd=str(tmp_path), poll_s=0.05)
    assert "kaboom detail" in str(exc.value)


def test_coding_run_pins_the_model(tmp_path, monkeypatch):
    """The coding run MUST pin --model claude-opus-4-8[1m]. 2026-06-13: with no
    --model it took the CLI default, which had flipped to Fable 5 (unavailable on
    this account) → every selfcode run exited 1. The full [1m] id (not the bare
    `opus` alias, which drops 1M context) keeps selfcode awake regardless of what
    the CLI default becomes next."""
    argv_log = tmp_path / "argv.txt"
    _stub_brain(tmp_path, monkeypatch,
                f'printf "%s\\n" "$@" > {argv_log}\ncat >/dev/null\nexit 0')
    ov.run_claude_supervised("task", cwd=str(tmp_path), poll_s=0.05)
    argv = argv_log.read_text().splitlines()
    assert "--model" in argv
    assert "claude-opus-4-8[1m]" in argv
    assert argv[argv.index("--model") + 1] == "claude-opus-4-8[1m]"


def test_runaway_run_is_cancelled_and_its_whole_tree_killed(tmp_path, monkeypatch):
    """A run past the time limit raises TimeoutExpired AND the whole process
    TREE is dead — a bare proc.kill() left grandchildren (claude's Bash tool)
    coding past the limit and holding the stdout pipe open (drain stall)."""
    pidfile = tmp_path / "child.pid"
    grandpidfile = tmp_path / "grandchild.pid"
    monkeypatch.setenv("PIDFILE", str(pidfile))
    monkeypatch.setenv("GRANDPIDFILE", str(grandpidfile))
    _stub_brain(tmp_path, monkeypatch,
                'echo $$ > "$PIDFILE"\nsleep 30 &\necho $! > "$GRANDPIDFILE"\nwait')
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        ov.run_claude_supervised("task", cwd=str(tmp_path), poll_s=0.05,
                                 time_limit_s=0.3)
    assert time.monotonic() - start < 10          # bounded, not the full 30s sleep
    time.sleep(0.2)                               # let the kernel reap the group
    for f in (pidfile, grandpidfile):
        pid = int(f.read_text().strip())
        with pytest.raises(ProcessLookupError):   # the runaway TREE is gone
            os.kill(pid, 0)


def test_child_dying_before_reading_stdin_reports_exit_not_pipe_crash(tmp_path, monkeypatch):
    """A child that exits without draining stdin used to surface as an unhandled
    BrokenPipeError mid-write; it must surface as the HONEST nonzero-exit error."""
    _stub_brain(tmp_path, monkeypatch, "exit 5")
    big_task = "x" * 200_000                      # larger than the OS pipe buffer
    with pytest.raises(RuntimeError, match="exited 5"):
        ov.run_claude_supervised(big_task, cwd=str(tmp_path), poll_s=0.05)


# ── limit pass-through (pure, injected) ──────────────────────────────────────
def test_supervise_honors_injected_time_limit():
    cancelled = []
    v = ov.supervise(is_alive=lambda: True, elapsed_fn=lambda: 2.0,
                     cancel_fn=lambda: cancelled.append(1), sleep_fn=lambda s: None,
                     time_limit_s=1.0)
    assert v.cancel is True and "time limit" in v.reason and cancelled == [1]


def test_supervise_honors_injected_cost_limit():
    v = ov.supervise(is_alive=lambda: True, elapsed_fn=lambda: 0.1,
                     cost_fn=lambda: 2.5, cancel_fn=lambda: None,
                     sleep_fn=lambda s: None, cost_limit_usd=2.0)
    assert v.cancel is True and "cost limit" in v.reason


def test_assess_cancels_exactly_at_the_boundary():
    """>= semantics: hitting the limit exactly is already over budget."""
    assert ov.assess(elapsed_s=300.0, time_limit_s=300.0).cancel is True
    assert ov.assess(elapsed_s=299.9, time_limit_s=300.0).cancel is False
    assert ov.assess(elapsed_s=1, cost_usd=10.0, cost_limit_usd=10.0).cancel is True
