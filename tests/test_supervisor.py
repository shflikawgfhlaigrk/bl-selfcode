"""The supervisor owns a SMALL SET of long-lived children (daemon + web deck),
not 46 flat KeepAlive jobs. Each child: spawn → readiness probe → liveness.
Death is intrinsic (proc exited); a *health* probe (zero-arg: ping / HTTP 200)
catches a wedged-but-alive child. Dead/wedged children restart with a per-child
circuit-break. Tests use fast real subprocesses + injectable probes.
"""
from __future__ import annotations

import sys

from utah.daemon.supervisor import ChildSpec, Supervisor

SLEEP = [sys.executable, "-c", "import time; time.sleep(30)"]
EXIT_NOW = [sys.executable, "-c", "raise SystemExit(0)"]
HEALTHY = lambda: True  # noqa: E731


def test_spawns_all_children_and_reports_ready():
    sup = Supervisor(children=[
        ChildSpec("a", SLEEP, probe=HEALTHY),
        ChildSpec("b", SLEEP, probe=HEALTHY),
    ], ready_timeout=3.0)
    try:
        assert sup.start_all() is True
        pids = sup.child_pids()
        assert len(pids) == 2 and all(pid > 0 for pid in pids.values())
    finally:
        sup.drain_all()


def test_drain_all_terminates_children():
    sup = Supervisor(children=[ChildSpec("a", SLEEP, probe=HEALTHY)], ready_timeout=3.0)
    sup.start_all()
    child = sup._children[0]
    sup.drain_all()
    assert child.proc is None or child.proc.poll() is not None


def test_restarts_a_child_that_died():
    sup = Supervisor(children=[ChildSpec("a", SLEEP, probe=HEALTHY)], ready_timeout=3.0)
    try:
        sup.start_all()
        first = sup._children[0].proc
        first.kill(); first.wait()
        sup.supervise_once()
        second = sup._children[0].proc
        assert second is not None and second.poll() is None
        assert second.pid != first.pid
    finally:
        sup.drain_all()


def test_restarts_a_wedged_child_failing_its_probe():
    """Alive process, but the health probe returns False → killed + restarted."""
    sup = Supervisor(
        children=[ChildSpec("a", SLEEP, probe=lambda: False)],
        ready_timeout=1.0, probe_grace_s=0.0,
    )
    try:
        sup._children[0].spawn()
        wedged = sup._children[0].proc
        sup.supervise_once()  # alive but probe False -> kill + restart
        restarted = sup._children[0].proc
        assert restarted.pid != wedged.pid
        assert restarted.poll() is None
    finally:
        sup.drain_all()


def test_wedged_child_restarts_only_after_consecutive_failures():
    """A single slow/failed probe must NOT restart a healthy-but-busy child —
    only N consecutive failures count as wedged (the false-positive-kill fix)."""
    state = {"healthy": False}
    sup = Supervisor(children=[ChildSpec("a", SLEEP, probe=lambda: state["healthy"])],
                     ready_timeout=1.0, probe_grace_s=0.0, wedge_after=3)
    try:
        sup._children[0].spawn()
        first = sup._children[0].proc
        sup.supervise_once()  # fail 1 — below threshold
        assert sup._children[0].proc.pid == first.pid
        sup.supervise_once()  # fail 2 — still below
        assert sup._children[0].proc.pid == first.pid
        sup.supervise_once()  # fail 3 — wedged → restart
        assert sup._children[0].proc.pid != first.pid
    finally:
        sup.drain_all()


def test_probe_success_resets_the_unhealthy_streak():
    state = {"healthy": False}
    sup = Supervisor(children=[ChildSpec("a", SLEEP, probe=lambda: state["healthy"])],
                     ready_timeout=1.0, probe_grace_s=0.0, wedge_after=3)
    try:
        sup._children[0].spawn()
        first = sup._children[0].proc
        sup.supervise_once(); sup.supervise_once()  # 2 fails
        state["healthy"] = True
        sup.supervise_once()  # success → streak resets
        state["healthy"] = False
        sup.supervise_once(); sup.supervise_once()  # 2 more fails (< 3 after reset)
        assert sup._children[0].proc.pid == first.pid  # not restarted
    finally:
        sup.drain_all()


def test_per_child_circuit_breaks_after_max_restarts():
    sup = Supervisor(
        children=[ChildSpec("crasher", EXIT_NOW, probe=HEALTHY)],
        max_restarts=3, window_s=60.0, ready_timeout=1.0,
    )
    try:
        sup._children[0].spawn()
        for _ in range(6):
            c = sup._children[0]
            if c.proc and c.proc.poll() is None:
                try:
                    c.proc.wait(timeout=2)  # ensure the fast crasher has exited
                except Exception:
                    pass
            sup.supervise_once()
        assert sup.circuit_broken() is True
    finally:
        sup.drain_all()


def test_http_probe_false_when_port_closed():
    """The web child's HTTP health probe returns False on a closed port."""
    from utah.daemon.supervisor import http_probe
    assert http_probe("http://127.0.0.1:9", timeout=0.5)() is False
