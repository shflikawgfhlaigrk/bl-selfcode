"""Supervisor failure edges with REAL processes: a spawn that fails (missing
binary) must never crash the supervisor loop or read as 'ready', drain must
escalate to kill when the graceful path raises, and probes are never-raises
boundaries. The supervisor IS the safety net — these pin that the net itself
has no unhandled edges."""
from __future__ import annotations

import subprocess
import sys

import pytest

from utah.daemon.supervisor import ChildSpec, Supervisor

SLEEP = [sys.executable, "-c", "import time; time.sleep(30)"]
MISSING = ["/nonexistent-utah-test-binary-xyz"]
HEALTHY = lambda: True  # noqa: E731


def _sup(spec: ChildSpec, **kw) -> Supervisor:
    kw.setdefault("ready_timeout", 3.0)
    return Supervisor(children=[spec], **kw)


# -- spawn failure ------------------------------------------------------------------

def test_spawn_failure_never_crashes_start_all_and_reads_not_ready():
    """A missing binary used to raise FileNotFoundError straight out of
    start_all — killing the supervisor itself. And with a passing probe,
    ready() reported True for a child that NEVER SPAWNED (a dishonest signal).
    """
    sup = _sup(ChildSpec("ghost", MISSING, probe=HEALTHY))
    try:
        assert sup.start_all() is False     # never raises; never claims ready
        assert sup.child_pids() == {}       # nothing is actually running
    finally:
        sup.drain_all()


def test_spawn_failure_in_supervise_once_counts_toward_the_breaker():
    """Repeated failed spawns must circuit-break like repeated crashes do —
    not loop forever, and never raise out of the supervision loop."""
    sup = _sup(ChildSpec("ghost", MISSING, probe=HEALTHY),
               max_restarts=3, window_s=60.0)
    try:
        sup.start_all()
        for _ in range(5):
            sup.supervise_once()            # must never raise
        assert sup.circuit_broken() is True
    finally:
        sup.drain_all()


def test_ready_is_false_fast_when_the_process_never_spawned():
    """ready() must not burn the full ready_timeout probing on behalf of a
    process that does not exist."""
    import time
    sup = _sup(ChildSpec("ghost", MISSING, probe=HEALTHY), ready_timeout=5.0)
    child = sup._children[0]
    child.spawn()
    t0 = time.monotonic()
    assert child.ready() is False
    assert time.monotonic() - t0 < 1.0      # immediate, not the 5s timeout


def test_circuit_broken_child_is_left_down_not_respawned():
    sup = _sup(ChildSpec("ghost", MISSING, probe=HEALTHY), max_restarts=2)
    try:
        sup.start_all()
        for _ in range(4):
            sup.supervise_once()
        assert sup.circuit_broken() is True
        sup.supervise_once()                # broken → skipped, no respawn attempt
        assert sup.child_pids() == {}
    finally:
        sup.drain_all()


# -- drain escalation ----------------------------------------------------------------

def test_drain_falls_back_to_kill_when_the_drain_fn_raises():
    def bad_drain():
        raise RuntimeError("control socket gone")

    sup = _sup(ChildSpec("a", SLEEP, probe=HEALTHY, drain=bad_drain))
    try:
        sup.start_all()
        child = sup._children[0]
        assert child.alive() is True
        sup.drain_all()                     # graceful path raises → escalate to kill
        assert child.proc.poll() is not None
    finally:
        sup.drain_all()


def test_drain_prefers_the_graceful_path_when_it_works():
    called: list[str] = []
    sup = _sup(ChildSpec("a", SLEEP, probe=HEALTHY))
    sup.start_all()
    child = sup._children[0]

    def good_drain():
        called.append("drain")
        child.proc.terminate()              # the graceful signal actually lands

    child.spec.drain = good_drain
    sup.drain_all()
    assert called == ["drain"]
    assert child.proc.poll() is not None


def test_kill_and_drain_on_a_never_spawned_child_are_noops():
    sup = _sup(ChildSpec("ghost", MISSING, probe=HEALTHY))
    child = sup._children[0]
    child.kill()                            # no proc — must not raise
    child.drain(1.0)


# -- probes are never-raises boundaries ----------------------------------------------

def test_ping_probe_is_false_when_the_control_call_raises(monkeypatch):
    """An unreachable daemon IS the signal the probe exists to report — the
    exception must become False, never escape into the supervision loop."""
    from utah.daemon import client as ctl
    from utah.daemon.supervisor import ping_probe

    def _dead(*_a, **_k):
        raise ConnectionRefusedError("socket gone")

    monkeypatch.setattr(ctl, "call_sync", _dead)
    assert ping_probe(timeout=0.5)() is False


def test_default_childspec_probe_is_healthy():
    """A spec with no probe must default to 'alive is healthy' (liveness-only
    children like voice) — not crash on the missing attribute."""
    spec = ChildSpec("bare", SLEEP)
    assert spec.probe() is True
