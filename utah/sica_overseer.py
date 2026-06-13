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

import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass

from utah import config, sica

log = logging.getLogger("utah.sica_overseer")

POLL_S = 30.0
#: consecutive unchanged-output polls that mean "stuck/looping" → cancel.
STALL_POLLS = 4
#: Bound on draining a finished/killed child's pipes — a grandchild that
#: inherited the pipe must not hang the cycle after the overseer already ruled.
DRAIN_TIMEOUT_S = float(os.environ.get("UTAH_OVERSEER_DRAIN_TIMEOUT", "30"))


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
              stall_polls: int = STALL_POLLS, time_limit_s: float | None = None,
              cost_limit_usd: float | None = None) -> Verdict:
    """Poll a running attempt until it ends or the overseer cancels it. Returns the
    final Verdict (cancel=False if the run finished on its own). All boundaries are
    injected so this is unit-proven without a real subprocess; ``time_limit_s`` /
    ``cost_limit_usd`` default to the paper limits (sica.TIME_LIMIT_S / COST_LIMIT_USD)."""
    limit_s = sica.TIME_LIMIT_S if time_limit_s is None else time_limit_s
    limit_usd = sica.COST_LIMIT_USD if cost_limit_usd is None else cost_limit_usd
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
                   time_limit_s=limit_s, cost_limit_usd=limit_usd,
                   stall_polls=stall_polls)
        if v.cancel:
            cancel_fn()
            return v
    return Verdict(False, "completed")


def _drain(proc: subprocess.Popen) -> tuple[str, str]:
    """Collect the child's remaining stdout/stderr, BOUNDED. After a kill (or a
    clean exit) ``communicate`` can still block if a grandchild inherited the
    pipes — escalate to a kill and give up on the text rather than hang the cycle."""
    try:
        return proc.communicate(timeout=DRAIN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        log.warning("overseer: pipe drain exceeded %.0fs — killing and abandoning output",
                    DRAIN_TIMEOUT_S)
        proc.kill()
        try:
            return proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover — kernel-level pipe wedge
            return "", ""


def run_claude_supervised(task: str, *, cwd: str, poll_s: float = POLL_S,
                          time_limit_s: float | None = None) -> None:
    """Run `claude -p` WITH coding tools under overseer supervision (Popen, so a
    runaway run is killed mid-flight). Raises subprocess.TimeoutExpired on an
    overseer cancel (→ sica τ penalty) and RuntimeError on a nonzero exit, so
    selfcode.propose discards the change exactly as for the plain runner.
    ``time_limit_s`` defaults to the paper wall-clock limit (sica.TIME_LIMIT_S)."""
    limit_s = sica.TIME_LIMIT_S if time_limit_s is None else time_limit_s
    # start_new_session: the run gets its OWN process group, so a cancel kills the
    # whole tree. A bare proc.kill() only hit the CLI itself — children it spawned
    # (its Bash tool) survived as orphans, kept coding past the limit, AND held the
    # stdout pipe open so the post-cancel drain stalled until they exited.
    proc = subprocess.Popen(
        [config.BRAIN_CMD, "-p", "--model", config.BRAIN_MODEL,
         "--allowedTools", "Edit", "Write", "Read", "Bash"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=cwd, text=True, start_new_session=True,
    )

    def _cancel() -> None:
        """SIGKILL the run's whole process group; fall back to the leader alone."""
        try:
            os.killpg(proc.pid, signal.SIGKILL)   # pgid == pid (start_new_session)
        except (ProcessLookupError, PermissionError):
            proc.kill()

    try:
        # A child that dies before draining stdin (bad CLI flags, instant crash)
        # surfaces here as BrokenPipeError mid-write — swallow it and fall through
        # to the HONEST exit-code handling below instead of crashing the cycle.
        proc.stdin.write(task)
        proc.stdin.close()
    except (BrokenPipeError, OSError) as exc:
        log.warning("claude run closed stdin early (will report its exit code): %s", exc)
    start = time.monotonic()
    verdict = supervise(
        is_alive=lambda: proc.poll() is None,
        elapsed_fn=lambda: time.monotonic() - start,
        cancel_fn=_cancel,
        poll_s=poll_s,
        time_limit_s=limit_s,
    )
    out, err = _drain(proc)
    if verdict.cancel:
        log.warning("overseer cancelled claude run: %s", verdict.reason)
        raise subprocess.TimeoutExpired(config.BRAIN_CMD, limit_s)
    if proc.returncode != 0:
        raise RuntimeError(f"claude coding run exited {proc.returncode}: "
                           f"{(err or out or '')[-300:]}")


__all__ = ["Verdict", "assess", "supervise", "run_claude_supervised", "POLL_S",
           "STALL_POLLS", "DRAIN_TIMEOUT_S"]
