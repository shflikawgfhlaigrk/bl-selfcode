"""Supervisor — one root parent, not 46 flat KeepAlive jobs.

Ace had 46 independent launchd jobs with blind KeepAlive: a chronic crasher
respawned forever (or launchd's backoff left it down for hours), and a
wedged-but-alive process read as "up". Utah's supervisor owns the daemon as a
child and:

* **probes liveness** (a real ``ping``, never pid-presence) — a wedged daemon is
  killed and restarted;
* **bounded backoff + circuit-break** — after too many restarts in a window it
  stops and alerts once, instead of thrashing;
* **reaps** the child (it is the parent — no zombies);
* **drains** the daemon on its own SIGTERM (verified child exit), then exits.

The supervisor is itself an flock singleton.
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

from utah.daemon import client as ctl, lifecycle, runtime

log = logging.getLogger("utah.supervisor")

SUP_LOCK = runtime.RUN_DIR / "utah-sup.lock"
SUP_PID = runtime.RUN_DIR / "utah-sup.pid"


class Supervisor:
    def __init__(
        self,
        *,
        max_restarts: int = 5,
        window_s: float = 60.0,
        ready_timeout: float = 20.0,
        probe_interval: float = 5.0,
        backoff_base: float = 0.5,
        backoff_max: float = 30.0,
        drain_timeout: float = 12.0,
    ) -> None:
        self._max_restarts = max_restarts
        self._window_s = window_s
        self._ready_timeout = ready_timeout
        self._probe_interval = probe_interval
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._drain_timeout = drain_timeout
        self._stop = threading.Event()
        self._child: subprocess.Popen | None = None
        self._restarts: collections.deque[float] = collections.deque()

    # -- child management ----------------------------------------------------
    def _spawn(self) -> None:
        # Child inherits env (incl. PYTHONPATH for `python -m utah.daemon.daemon`).
        self._child = subprocess.Popen(
            [sys.executable, "-m", "utah.daemon.daemon"], env={**os.environ}
        )
        log.info("spawned daemon child pid=%d", self._child.pid)

    def _ready(self) -> bool:
        deadline = time.monotonic() + self._ready_timeout
        while time.monotonic() < deadline and not self._stop.is_set():
            if self._child and self._child.poll() is not None:
                return False  # died during boot
            try:
                ctl.call_sync("ping", timeout=2.0)
                return True
            except Exception:
                self._stop.wait(0.3)
        return False

    def _record_restart(self) -> None:
        now = time.monotonic()
        self._restarts.append(now)
        while self._restarts and now - self._restarts[0] > self._window_s:
            self._restarts.popleft()

    def _circuit_broken(self) -> bool:
        return len(self._restarts) >= self._max_restarts

    def _drain_child(self) -> None:
        child = self._child
        if not child or child.poll() is not None:
            return
        try:
            ctl.call_sync("shutdown", timeout=3.0)
        except Exception:
            pass
        try:
            child.wait(timeout=self._drain_timeout)
            return
        except subprocess.TimeoutExpired:
            log.warning("daemon did not drain in %.0fs → SIGTERM", self._drain_timeout)
        child.terminate()
        try:
            child.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            log.error("daemon ignored SIGTERM → SIGKILL")
            child.kill()
            child.wait()

    def _kill_child(self) -> None:
        child = self._child
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()

    # -- main loop -----------------------------------------------------------
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

        backoff = self._backoff_base
        while not self._stop.is_set():
            self._spawn()
            if not self._ready():
                log.error("daemon failed to become ready")
                self._kill_child()
                self._record_restart()
                if self._circuit_broken():
                    break
                self._stop.wait(backoff)
                backoff = min(backoff * 2, self._backoff_max)
                continue
            log.info("daemon healthy")
            backoff = self._backoff_base  # reset on a good boot

            # monitor until the child dies, a probe fails, or we're told to stop
            while not self._stop.is_set():
                rc = self._child.poll() if self._child else 0
                if rc is not None:
                    log.warning("daemon exited rc=%s → restart", rc)
                    self._record_restart()
                    break
                try:
                    ctl.call_sync("ping", timeout=3.0)
                except Exception as exc:
                    log.warning("liveness probe failed (%s) → killing wedged daemon", exc)
                    self._kill_child()
                    self._record_restart()
                    break
                self._stop.wait(self._probe_interval)

            if self._stop.is_set():
                break
            if self._circuit_broken():
                log.error(
                    "circuit-break: %d restarts in %.0fs — stopping (alert once)",
                    len(self._restarts), self._window_s,
                )
                break

        log.info("supervisor draining daemon and exiting")
        self._drain_child()
        return 0


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
    return Supervisor().run()


if __name__ == "__main__":
    raise SystemExit(main())
