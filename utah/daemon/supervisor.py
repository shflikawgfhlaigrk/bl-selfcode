"""Supervisor — one root parent owning a SMALL SET of long-lived children.

Ace had 46 independent launchd jobs with blind KeepAlive: a chronic crasher
respawned forever (or launchd's backoff left it down for hours), and a
wedged-but-alive process read as "up". Utah's supervisor owns the long-lived
core — the **daemon** and the **web deck** — as children and:

* **probes health** (a real ``ping`` / HTTP 200, never pid-presence) — a wedged
  child is killed and restarted; death is detected intrinsically (proc exited);
* **bounded backoff + per-child circuit-break** — after too many restarts in a
  window it stops restarting that child and alerts once, instead of thrashing;
* **reaps** children (it is the parent — no zombies);
* **drains** each child on shutdown (the daemon gets a graceful ``shutdown``).

The supervisor is an flock singleton; launchd keeps the *supervisor* itself
alive across crashes/reboots (RunAtLoad + throttled KeepAlive). Everything else
is supervised here, not as a flat launchd job (doc 2-processes).
"""
from __future__ import annotations

import collections
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable, Sequence

from utah.daemon import client as ctl, lifecycle, runtime

log = logging.getLogger("utah.supervisor")

SUP_LOCK = runtime.RUN_DIR / "utah-sup.lock"
SUP_PID = runtime.RUN_DIR / "utah-sup.pid"

#: Cap on the in-place recovery backoff (B1′). The supervisor never exits on an
#: all-children-down circuit-break; it retries with backoff up to this ceiling.
MAX_RECOVERY_BACKOFF_S: float = 60.0

Probe = Callable[[], bool]


def _alert_children_down(names: Sequence[str]) -> None:
    """Page Michael ONCE when every supervised child is circuit-broken (B1′). The
    supervisor is still alive and recovering in place; this is the heads-up that
    daemon/web/voice are flapping. Best-effort — alerting must never crash the loop."""
    try:
        from utah import alerts

        alerts.critical_async(
            "supervisor",
            f"all supervised children down ({', '.join(names)}) — recovering in place",
            key="supervisor/all_children_down",
        )
    except Exception:  # noqa: BLE001
        log.warning("supervisor down-alert failed", exc_info=True)


def ping_probe(timeout: float = 2.0) -> Probe:
    """Health probe for the daemon: a real control-socket ``ping``."""
    def _p() -> bool:
        try:
            ctl.call_sync("ping", timeout=timeout)
            return True
        except Exception:
            return False
    return _p


def http_probe(url: str, timeout: float = 2.0) -> Probe:
    """Health probe for an HTTP service (the web deck): GET returns < 500."""
    def _p() -> bool:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return 200 <= getattr(resp, "status", 200) < 500
        except Exception:
            return False
    return _p


def voice_audio_probe(deaf_after_s: float | None = None) -> Probe:
    """Audio-liveness probe for the voice child (B15). A deaf-but-ALIVE loop — the mic
    device delivering pure zeros while the process keeps running — passes a liveness check
    but is useless. This probe reads the loop's voice-state heartbeat and reports the loop
    WEDGED (probe False → kill + restart) when the mic has been deaf past the threshold, so
    reopening the audio stream recovers a wedged CoreAudio handle. Healthy when there's no
    heartbeat yet (startup; grace + liveness handle that), when audio is flowing, or when
    the loop is only intentionally muted (echo guard). A frozen heartbeat on a live process
    (the monitor thread wedged) is also treated as wedged."""
    from utah import config
    from utah.voice import state as voice_state

    threshold = config.VOICE_DEAF_RESTART_S if deaf_after_s is None else deaf_after_s

    def _p() -> bool:
        st = voice_state.read()
        if not st or "ts" not in st:
            return True                                   # no heartbeat yet — not our call
        age = time.time() - float(st["ts"])
        if age > threshold:
            return False                                  # heartbeat frozen → monitor wedged
        if not st.get("deaf"):
            return True                                   # mic alive (zeros never sustained)
        return float(st.get("mic_quiet_s", 0.0)) < threshold
    return _p


def _daemon_drain() -> None:
    ctl.call_sync("shutdown", timeout=3.0)


@dataclass
class ChildSpec:
    """One supervised long-lived child: how to start it and how to health-check it."""

    name: str
    argv: Sequence[str]
    probe: Probe = lambda: True
    drain: Callable[[], None] | None = None
    #: Extra env merged over ``os.environ`` for this child (e.g. the voice child
    #: runs from a signed .app bundle and needs PYTHONHOME/PYTHONPATH set).
    env: dict[str, str] | None = None


class _ManagedChild:
    def __init__(
        self, spec: ChildSpec, *, ready_timeout: float, max_restarts: int,
        window_s: float, probe_grace_s: float, wedge_after: int = 1,
    ) -> None:
        self.spec = spec
        self.proc: subprocess.Popen | None = None
        self._ready_timeout = ready_timeout
        self._max = max_restarts
        self._window = window_s
        self._grace = probe_grace_s
        self._wedge_after = max(1, wedge_after)
        self._unhealthy = 0
        self._restarts: collections.deque[float] = collections.deque()
        self._spawned_at = 0.0

    def spawn(self) -> None:
        env = {**os.environ, **(self.spec.env or {})}
        self.proc = subprocess.Popen(list(self.spec.argv), env=env)
        self._spawned_at = time.monotonic()
        log.info("spawned %s pid=%d", self.spec.name, self.proc.pid)

    def ready(self) -> bool:
        deadline = time.monotonic() + self._ready_timeout
        while time.monotonic() < deadline:
            if self.proc and self.proc.poll() is not None:
                return False  # died during boot
            if self.spec.probe():
                return True
            time.sleep(0.2)
        return False

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def healthy(self) -> bool:
        # within the post-spawn grace window, don't probe (let it come up)
        if time.monotonic() - self._spawned_at < self._grace:
            return True
        return self.spec.probe()

    def record_restart(self) -> None:
        now = time.monotonic()
        self._restarts.append(now)
        while self._restarts and now - self._restarts[0] > self._window:
            self._restarts.popleft()

    def circuit_broken(self) -> bool:
        return len(self._restarts) >= self._max

    def reset_circuit(self) -> None:
        """Clear the restart history so the child gets a fresh set of attempts. Used by
        the in-place recovery loop after an all-children-down backoff (B1′) — the
        supervisor never exits, so it must re-arm its own breakers to keep trying."""
        self._restarts.clear()
        self._unhealthy = 0

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()

    def drain(self, drain_timeout: float = 12.0) -> None:
        if not self.proc or self.proc.poll() is not None:
            return
        if self.spec.drain is not None:
            try:
                self.spec.drain()
                self.proc.wait(timeout=drain_timeout)
                return
            except Exception:
                pass
        self.kill()


class Supervisor:
    def __init__(
        self,
        children: Sequence[ChildSpec],
        *,
        max_restarts: int = 5,
        window_s: float = 60.0,
        ready_timeout: float = 20.0,
        probe_interval: float = 5.0,
        drain_timeout: float = 12.0,
        probe_grace_s: float = 3.0,
        wedge_after: int = 1,
    ) -> None:
        self._children = [
            _ManagedChild(
                s, ready_timeout=ready_timeout, max_restarts=max_restarts,
                window_s=window_s, probe_grace_s=probe_grace_s, wedge_after=wedge_after,
            )
            for s in children
        ]
        self._probe_interval = probe_interval
        self._drain_timeout = drain_timeout
        self._stop = threading.Event()

    # -- lifecycle ----------------------------------------------------------
    def start_all(self) -> bool:
        for child in self._children:
            child.spawn()
        ok = True
        for child in self._children:
            if not child.ready():
                log.error("%s failed to become ready", child.spec.name)
                ok = False
            else:
                log.info("%s healthy", child.spec.name)
        return ok

    def supervise_once(self) -> None:
        for child in self._children:
            if child.circuit_broken():
                continue  # gave up on this child; already alerted
            if not child.alive():
                reason = "died"
            elif not child.healthy():
                child._unhealthy += 1
                if child._unhealthy < child._wedge_after:
                    continue  # one slow/failed probe is tolerated — not yet wedged
                reason = "wedged"
                child.kill()
            else:
                child._unhealthy = 0  # healthy probe resets the streak
                continue
            log.warning(
                "%s %s → restart (unhealthy_streak=%d)", child.spec.name, reason, child._unhealthy
            )
            child._unhealthy = 0
            child.record_restart()
            if child.circuit_broken():
                log.error(
                    "circuit-break: %s restarted %d× in %.0fs — leaving down (alert once)",
                    child.spec.name, child._max, child._window,
                )
                continue
            child.spawn()

    def circuit_broken(self) -> bool:
        return any(c.circuit_broken() for c in self._children)

    def child_pids(self) -> dict[str, int]:
        return {
            c.spec.name: c.proc.pid
            for c in self._children
            if c.proc and c.proc.poll() is None
        }

    def drain_all(self) -> None:
        for child in self._children:
            child.drain(self._drain_timeout)

    # -- main loop ----------------------------------------------------------
    def run(self) -> int:
        runtime.ensure_runtime()
        try:
            lifecycle.Singleton(SUP_LOCK).acquire()
        except lifecycle.AlreadyRunning as exc:
            print(f"supervisor already running (pid {exc.pid})", file=sys.stderr)
            return 1
        SUP_PID.write_text(f"{os.getpid()}\n")
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: self._stop.set())

        if not self.start_all():
            log.error("not all children became ready on boot")

        # In-place recovery (B1′): the supervisor NEVER exits on an all-children-down
        # circuit-break — exiting handed the core to a 1–3 min launchd-backoff blackout.
        # Instead it stays up, alerts ONCE, and keeps retrying with escalating backoff,
        # re-arming its own breakers each round, so there is no unsupervised gap.
        recovery_backoff = self._probe_interval
        sup_down_alerted = False
        while not self._stop.is_set():
            self.supervise_once()
            all_broken = bool(self._children) and all(c.circuit_broken() for c in self._children)
            if all_broken:
                if not sup_down_alerted:
                    _alert_children_down([c.spec.name for c in self._children])
                    sup_down_alerted = True
                log.error(
                    "all children circuit-broken — retrying IN PLACE after %.0fs "
                    "(supervisor stays up; no unsupervised gap)", recovery_backoff,
                )
                if self._stop.wait(recovery_backoff):
                    break
                recovery_backoff = min(recovery_backoff * 2, MAX_RECOVERY_BACKOFF_S)
                for child in self._children:        # re-arm and try to bring each back
                    child.reset_circuit()
                    if not child.alive():
                        child.spawn()
                continue
            # at least one child is healthy → recovered: reset the alert + backoff so a
            # future outage re-alerts and starts fast again.
            sup_down_alerted = False
            recovery_backoff = self._probe_interval
            if self._stop.wait(self._probe_interval):
                break

        log.info("supervisor draining children and exiting")
        self.drain_all()
        return 0


def _voice_spec() -> ChildSpec:
    """Voice child, launched from the signed ``com.utah.voice`` bundle when on mac.

    The bundle gives the launchd-spawned loop a TCC identity that can hold a
    microphone grant; without it the framework-python child is deaf under
    launchd. Falls back to ``sys.executable`` on non-mac or build failure.
    """
    argv: list[str] = [sys.executable, "-m", "utah.voice.loop"]
    env: dict[str, str] | None = None
    try:
        from utah.voice import macapp

        info = macapp.ensure()
        if info is not None:
            argv = [info.exec_path, "-m", "utah.voice.loop"]
            env = info.env
            log.info("voice: launching via signed bundle %s", info.exec_path)
        else:
            log.warning(
                "voice: app bundle unavailable — using %s (mic likely deaf under launchd)",
                sys.executable,
            )
    except Exception:  # noqa: BLE001
        log.warning("voice: bundle setup failed — using sys.executable", exc_info=True)
    # B15: a real audio-liveness probe — a deaf-but-alive loop is restarted, not left deaf.
    return ChildSpec("voice", argv, env=env, probe=voice_audio_probe())


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.handlers.RotatingFileHandler(
                runtime.LOG_DIR / "utah-sup.log", maxBytes=4 * 1024 * 1024, backupCount=2
            ),
            logging.StreamHandler(sys.stderr),
        ],
    )
    children = [
        ChildSpec(
            "daemon",
            [sys.executable, "-m", "utah.daemon.daemon"],
            probe=ping_probe(5.0),
            drain=_daemon_drain,
        ),
        ChildSpec(
            "web",
            [sys.executable, "-m", "utah.interface.web"],
            probe=http_probe("http://127.0.0.1:8766/", 5.0),
        ),
        # Always-on voice: wake "ace" -> Moonshine STT -> brain -> Piper -> chat box.
        # No health endpoint — supervised by liveness (the loop is resilient and
        # never fast-exits, so it's only restarted if the process actually dies).
        # Launched from a signed com.utah.voice .app so macOS grants it the mic
        # under launchd (a bare framework-python child is deaf — see voice/macapp).
        _voice_spec(),
    ]
    # Tolerant of a busy/cold-booting Mac: 8s post-spawn grace, 5s probe timeout,
    # and only wedge after 3 consecutive failed probes (~15s) — never kill a
    # healthy-but-slow child on one slow ping (the self-inflicted-outage fix).
    return Supervisor(
        children, ready_timeout=30.0, probe_grace_s=8.0, probe_interval=5.0, wedge_after=3
    ).run()


if __name__ == "__main__":
    raise SystemExit(main())
