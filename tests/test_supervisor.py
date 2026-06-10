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


def test_reset_circuit_rearms_the_breaker_for_in_place_recovery():
    """B1′: the in-place recovery loop re-arms a circuit-broken child so the supervisor
    can keep trying without exiting (no unsupervised launchd-backoff gap)."""
    sup = Supervisor(
        children=[ChildSpec("crasher", EXIT_NOW, probe=HEALTHY)],
        max_restarts=3, window_s=60.0, ready_timeout=1.0,
    )
    child = sup._children[0]
    for _ in range(3):
        child.record_restart()
    assert child.circuit_broken() is True
    child.reset_circuit()                       # the recovery loop's re-arm
    assert child.circuit_broken() is False      # ready to try again, no exit


def test_children_down_alert_is_best_effort(monkeypatch):
    """The all-children-down page must never crash the recovery loop."""
    from utah.daemon import supervisor as sup_mod

    sent = []
    import utah.alerts as alerts
    monkeypatch.setattr(alerts, "critical_async",
                        lambda src, detail, key=None: sent.append((src, key)))
    sup_mod._alert_children_down(["daemon", "web", "voice"])
    assert sent and sent[0][1] == "supervisor/all_children_down"
    # even if alerting blows up, it is swallowed
    monkeypatch.setattr(alerts, "critical_async",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    sup_mod._alert_children_down(["daemon"])     # must not raise


def test_http_probe_false_when_port_closed():
    """The web child's HTTP health probe returns False on a closed port."""
    from utah.daemon.supervisor import http_probe
    assert http_probe("http://127.0.0.1:9", timeout=0.5)() is False


def test_voice_audio_probe_restarts_a_deaf_but_alive_loop(monkeypatch):
    """B15: a fresh heartbeat that reports the mic deaf past the threshold → probe False
    (wedged → restart). A healthy/quiet mic or a startup with no heartbeat → True."""
    import time as _t

    from utah.daemon import supervisor as sup_mod
    from utah.voice import state as voice_state

    now = _t.time()
    probe = sup_mod.voice_audio_probe(deaf_after_s=75.0)

    monkeypatch.setattr(voice_state, "read", lambda: None)
    assert probe() is True                                   # no heartbeat → not our call

    monkeypatch.setattr(voice_state, "read",
                        lambda: {"ts": now, "deaf": False, "mic_quiet_s": 0.0})
    assert probe() is True                                   # mic alive

    monkeypatch.setattr(voice_state, "read",
                        lambda: {"ts": now, "deaf": True, "mic_quiet_s": 90.0})
    assert probe() is False                                  # deaf past threshold → restart

    monkeypatch.setattr(voice_state, "read",
                        lambda: {"ts": now, "deaf": True, "mic_quiet_s": 40.0})
    assert probe() is True                                   # deaf but not yet past threshold

    monkeypatch.setattr(voice_state, "read",
                        lambda: {"ts": now - 999, "deaf": False, "mic_quiet_s": 0.0})
    assert probe() is False                                  # frozen heartbeat → monitor wedged


def test_child_spec_env_is_merged_into_subprocess(tmp_path):
    """Per-child env (the voice child's PYTHONHOME/PYTHONPATH) reaches the process.

    Without this, the signed-bundle interpreter can't find its stdlib/deps and
    the mic fix is moot.
    """
    out = tmp_path / "env.txt"
    argv = [sys.executable, "-c",
            "import os,sys; open(sys.argv[1],'w').write(os.environ.get('UTAH_TEST_ENV','MISSING'))",
            str(out)]
    sup = Supervisor(
        children=[ChildSpec("e", argv, probe=HEALTHY, env={"UTAH_TEST_ENV": "from-childspec"})],
        ready_timeout=3.0,
    )
    try:
        sup.start_all()
        sup._children[0].proc.wait(timeout=5)
    finally:
        sup.drain_all()
    assert out.read_text() == "from-childspec"


def test_voice_spec_is_constructible():
    """``_voice_spec`` builds a ChildSpec (bundle on mac, sys.executable fallback)."""
    from utah.daemon.supervisor import _voice_spec
    spec = _voice_spec()
    assert spec.name == "voice"
    assert spec.argv[-2:] == ["-m", "utah.voice.loop"]
