"""Process lifecycle — flock singleton, pidfile, verified hard-exit.

Ace's worst outage was the **zombie daemon**: ``stop()`` returned but the
process didn't actually exit, so launchd saw it "alive" and never respawned.
Utah refuses that failure mode:

* **Singleton = an ``flock``** (kernel-held, auto-released the instant the
  process dies — even on SIGKILL/crash). The lockfile is **never deleted**
  (deleting it races); it also carries the live pid for introspection.
* **Verified hard exit:** when draining starts we arm a hard-exit timer, and
  ``main`` ends with ``os._exit`` — the process is *guaranteed* to terminate,
  never half-dead. Liveness is a probe (``ping``), never pid-presence.
"""
from __future__ import annotations

import fcntl
import logging
import os
import threading
from pathlib import Path

from utah import UtahError
from utah.daemon import runtime

log = logging.getLogger("utah.daemon.lifecycle")


class AlreadyRunning(UtahError):
    def __init__(self, pid: int | None) -> None:
        self.pid = pid
        super().__init__(f"utah daemon already running (pid {pid})")


class Singleton:
    """An flock-based singleton. Hold the instance for the process lifetime."""

    def __init__(self, lock_path: Path = runtime.LOCK_PATH) -> None:
        self._path = lock_path
        self._fd: int | None = None

    def acquire(self) -> "Singleton":
        if self._fd is not None:
            # Idempotent for the holder: a second os.open would create a NEW
            # open-file-description, and the kernel would refuse our own lock —
            # the holder must never AlreadyRunning against itself.
            return self
        runtime.ensure_runtime()
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            held = _read_int(fd)
            os.close(fd)
            raise AlreadyRunning(held) from exc
        # We own it. Record our pid in the lockfile — introspection ONLY: a
        # failed write must never surrender the flock (the actual exclusion).
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode())
            os.fsync(fd)
        except OSError:
            log.warning("could not record pid in %s (lock still held)", self._path)
        self._fd = fd  # kept open → lock auto-releases when the process dies
        return self

    def release(self) -> None:
        """Rarely needed — process exit auto-releases. NEVER unlinks the file."""
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


def _read_int(fd: int) -> int | None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        data = os.read(fd, 32).decode().strip()
        return int(data) if data else None
    except (OSError, ValueError):
        return None


def live_pid() -> int | None:
    """Best-effort read of the daemon's pid from the lockfile (introspection)."""
    try:
        fd = os.open(runtime.LOCK_PATH, os.O_RDONLY)
    except OSError:
        return None
    try:
        return _read_int(fd)
    finally:
        os.close(fd)


def write_pidfile() -> None:
    """Atomic pidfile write — introspection only (liveness is a probe), so an
    unwritable path is logged, never a daemon-boot crash."""
    try:
        tmp = runtime.PID_PATH.with_suffix(".tmp")
        tmp.write_text(f"{os.getpid()}\n")
        os.replace(tmp, runtime.PID_PATH)
    except OSError:
        log.warning("could not write pidfile %s (introspection only)", runtime.PID_PATH)


def arm_hard_exit(timeout_s: float, code: int = 0) -> threading.Timer:
    """Guarantee termination: if a clean drain hangs past *timeout_s*, force
    ``os._exit``. This is the structural kill of the zombie-daemon class.

    A non-positive timeout would fire the exit immediately — that is always a
    caller bug (it would kill the process before the drain even starts), so it
    is rejected loudly instead of armed.
    """
    if timeout_s <= 0:
        raise ValueError(f"hard-exit timeout must be > 0s, got {timeout_s}")
    timer = threading.Timer(timeout_s, lambda: os._exit(code))
    timer.daemon = True
    timer.start()
    return timer
