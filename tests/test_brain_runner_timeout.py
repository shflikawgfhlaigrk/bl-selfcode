"""The REAL streaming subprocess runner under hostile child behavior.

The injectable-runner tests (test_brain_stream.py) never exercise the actual
subprocess boundary. These do, with real child processes and a wall clock:

- a child that hangs SILENTLY (no output lines) must be killed at the deadline
  and raise BrainUnavailable — the old in-loop check only ran between lines, so
  a zero-output hang blocked forever and then returned an EMPTY stream (no
  timeout, no error: a silent blank).
- a consumer that abandons the generator must not leak a live CLI process
  (an orphan `claude -p` burns the paid lane with no one reading it).
"""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

from utah import brain


def test_silent_hang_times_out_instead_of_blocking_forever():
    """No stdout lines at all + a hung child: the runner must enforce the
    deadline itself (kill + structured timeout), not wait for the child."""
    t0 = time.monotonic()
    with pytest.raises(brain.BrainUnavailable, match="timed out"):
        list(brain._subprocess_stream_runner(
            [sys.executable, "-c", "import time; time.sleep(6)"], 1))
    # well under the child's 6s sleep — the watchdog fired, we didn't just wait
    assert time.monotonic() - t0 < 4.0


def test_hang_after_partial_output_still_times_out():
    """Lines, then silence: the deadline must fire mid-stream too."""
    t0 = time.monotonic()
    with pytest.raises(brain.BrainUnavailable, match="timed out"):
        list(brain._subprocess_stream_runner(
            [sys.executable, "-u", "-c",
             "print('first'); import time; time.sleep(6)"], 1))
    assert time.monotonic() - t0 < 4.0


def test_closing_the_generator_kills_the_child(monkeypatch):
    """Caller walks away (voice barge-in, dropped SSE) → the CLI must die with
    it, not keep generating to a closed pipe."""
    captured: dict = {}
    real_popen = subprocess.Popen

    def spy(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        captured["proc"] = proc
        return proc

    monkeypatch.setattr(brain.subprocess, "Popen", spy)
    gen = brain._subprocess_stream_runner(
        [sys.executable, "-u", "-c",
         "print('x'); import time; time.sleep(30)"], 30)
    assert next(gen).strip() == "x"
    gen.close()
    proc = captured["proc"]
    deadline = time.monotonic() + 3.0
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert proc.poll() is not None, "child still running after generator close"


def test_fast_clean_exit_is_unaffected_by_the_watchdog():
    out = list(brain._subprocess_stream_runner(
        [sys.executable, "-c", "print('a'); print('b')"], 10))
    assert [s.strip() for s in out] == ["a", "b"]


def test_nonzero_exit_after_watchdog_armed_is_still_exit_not_timeout():
    with pytest.raises(brain.BrainUnavailable, match="exited 7"):
        list(brain._subprocess_stream_runner(
            [sys.executable, "-c", "import sys; sys.exit(7)"], 10))
