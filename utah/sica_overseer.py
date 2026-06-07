"""SICA overseer (rung 3) — safety monitor for a running self-code attempt.

Paper §overseer (arXiv 2504.15228): a monitor polls a live run every ~poll_s and
CANCELS it when it breaches a hard limit (300s wall / $10 cost) or looks
stuck/looping (output unchanged for several consecutive polls). A cancelled run
is treated as a timeout — sica.utility applies the τ=0.5 penalty.

Pure decision (:func:`assess`) + a :func:`supervise` loop with every boundary
injected (alive/elapsed/output/cost/cancel/sleep) so it is fully unit-proven,
plus :func:`run_claude_supervised` — the real Popen-based coding runner the
autonomous lane uses so a runaway `claude -p` can be killed mid-run.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

from utah import config, sica

POLL_S = 30.0
#: consecutive unchanged-output polls that mean "stuck/looping" → cancel.
STALL_POLLS = 4


@dataclass
class Verdict:
    cancel: bool
    reason: str


def assess(*, elapsed_s: float, cost_usd: float = 0.0, stall_count: int = 0,
           time_limit_s: float = sica.TIME_LIMIT_S,
           cost_limit_usd: float = sica.COST_LIMIT_USD,
           stall_polls: int = STALL_POLLS) -> Verdict:
    """Decide whether to cancel a run given its current vitals (pure)."""
    if elapsed_s >= time_limit_s:
        return Verdict(True, f"time limit {time_limit_s:.0f}s exceeded ({elapsed_s:.0f}s)")
    if cost_usd >= cost_limit_usd:
        return Verdict(True, f"cost limit ${cost_limit_usd:.0f} exceeded (${cost_usd:.2f})")
    if stall_count >= stall_polls:
        return Verdict(True, f"stalled: output unchanged for {stall_count} polls")
    return Verdict(False, "ok")


def supervise(*, is_alive, elapsed_fn, cancel_fn, output_fn=lambda: None,
              cost_fn=lambda: 0.0, poll_s: float = POLL_S, sleep_fn=time.sleep,
              stall_polls: int = STALL_POLLS) -> Verdict:
    """Poll a running attempt until it ends or the overseer cancels it. Returns the
    final Verdict (cancel=False if the run finished on its own). All boundaries are
    injected so this is unit-proven without a real subprocess."""
    last_output = None
    stall = 0
    while is_alive():
        sleep_fn(poll_s)
        if not is_alive():
            break
        out = output_fn()
        if out is not None and out == last_output:
            stall += 1
        else:
            stall = 0
            last_output = out
        v = assess(elapsed_s=elapsed_fn(), cost_usd=cost_fn(), stall_count=stall,
                   stall_polls=stall_polls)
        if v.cancel:
            cancel_fn()
            return v
    return Verdict(False, "completed")


def run_claude_supervised(task: str, *, cwd: str, poll_s: float = POLL_S) -> None:
    """Run `claude -p` WITH coding tools under overseer supervision (Popen, so a
    runaway run is killed mid-flight). Raises subprocess.TimeoutExpired on an
    overseer cancel (→ sica τ penalty) and RuntimeError on a nonzero exit, so
    selfcode.propose discards the change exactly as for the plain runner."""
    proc = subprocess.Popen(
        [config.BRAIN_CMD, "-p", "--allowedTools", "Edit", "Write", "Read", "Bash"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=cwd, text=True,
    )
    assert proc.stdin is not None
    proc.stdin.write(task)
    proc.stdin.close()
    start = time.monotonic()
    verdict = supervise(
        is_alive=lambda: proc.poll() is None,
        elapsed_fn=lambda: time.monotonic() - start,
        cancel_fn=proc.kill,
        poll_s=poll_s,
    )
    out, err = proc.communicate()
    if verdict.cancel:
        raise subprocess.TimeoutExpired(config.BRAIN_CMD, sica.TIME_LIMIT_S)
    if proc.returncode != 0:
        raise RuntimeError(f"claude coding run exited {proc.returncode}: "
                           f"{(err or out or '')[-300:]}")


__all__ = ["Verdict", "assess", "supervise", "run_claude_supervised", "POLL_S", "STALL_POLLS"]
