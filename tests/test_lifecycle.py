"""Lifecycle invariants on REAL files and processes: the flock singleton
(kernel-held, contended across open-file-descriptions), pid introspection that
never lies, and the verified hard exit proven in a real subprocess."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from utah.daemon import lifecycle, runtime
from utah.daemon.lifecycle import AlreadyRunning, Singleton

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def lock_path(tmp_path: Path) -> Path:
    return tmp_path / "utahd.lock"


# -- Singleton ----------------------------------------------------------------

def test_acquire_records_pid_in_lockfile(lock_path):
    s = Singleton(lock_path).acquire()
    try:
        assert lock_path.read_text().strip() == str(os.getpid())
    finally:
        s.release()


def test_lockfile_is_owner_only(lock_path):
    s = Singleton(lock_path).acquire()
    try:
        assert (lock_path.stat().st_mode & 0o777) == 0o600
    finally:
        s.release()


def test_second_acquire_raises_already_running_with_holder_pid(lock_path):
    """flock conflicts across open-file-descriptions, so contention is provable
    in-process: a second Singleton on the same path must be refused."""
    s1 = Singleton(lock_path).acquire()
    try:
        with pytest.raises(AlreadyRunning) as exc:
            Singleton(lock_path).acquire()
        assert exc.value.pid == os.getpid()
        assert str(os.getpid()) in str(exc.value)
    finally:
        s1.release()


def test_release_allows_reacquire_but_never_unlinks(lock_path):
    s1 = Singleton(lock_path).acquire()
    s1.release()
    assert lock_path.exists()  # deleting the lockfile races — it must survive
    s2 = Singleton(lock_path).acquire()  # and a successor can take the lock
    s2.release()


def test_acquire_is_idempotent_for_the_holder(lock_path):
    """Re-acquiring a lock this instance already holds must be a no-op — a second
    os.open creates a NEW open-file-description, so without the guard the holder
    would raise AlreadyRunning against itself (self-deadlock)."""
    s = Singleton(lock_path).acquire()
    try:
        assert s.acquire() is s
    finally:
        s.release()


def test_release_is_idempotent(lock_path):
    s = Singleton(lock_path).acquire()
    s.release()
    s.release()  # second release must be a no-op, not EBADF


def test_pid_record_failure_keeps_the_lock(lock_path, monkeypatch):
    """The pid in the lockfile is introspection only — if recording it fails,
    the flock (the actual mutual exclusion) must still be held."""
    monkeypatch.setattr(
        lifecycle.os, "ftruncate",
        lambda *a: (_ for _ in ()).throw(OSError("disk full")),
    )
    s = Singleton(lock_path).acquire()  # must not raise
    monkeypatch.undo()
    try:
        with pytest.raises(AlreadyRunning):
            Singleton(lock_path).acquire()  # the lock is genuinely held
    finally:
        s.release()


# -- live_pid -----------------------------------------------------------------

def test_live_pid_missing_lockfile_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "LOCK_PATH", tmp_path / "absent.lock")
    assert lifecycle.live_pid() is None


def test_live_pid_reads_the_holder(lock_path, monkeypatch):
    monkeypatch.setattr(runtime, "LOCK_PATH", lock_path)
    s = Singleton(lock_path).acquire()
    try:
        assert lifecycle.live_pid() == os.getpid()
    finally:
        s.release()


def test_live_pid_garbage_content_is_none_not_a_crash(lock_path, monkeypatch):
    monkeypatch.setattr(runtime, "LOCK_PATH", lock_path)
    lock_path.write_text("not-a-pid\n")
    assert lifecycle.live_pid() is None


# -- pidfile ------------------------------------------------------------------

def test_write_pidfile_is_atomic(tmp_path, monkeypatch):
    pid_path = tmp_path / "utahd.pid"
    monkeypatch.setattr(runtime, "PID_PATH", pid_path)
    lifecycle.write_pidfile()
    assert pid_path.read_text().strip() == str(os.getpid())
    assert not pid_path.with_suffix(".tmp").exists()  # replaced, never left behind


def test_write_pidfile_failure_never_stops_boot(tmp_path, monkeypatch):
    """The pidfile is introspection only — an unwritable path is logged, not
    a daemon-boot crash."""
    monkeypatch.setattr(runtime, "PID_PATH", tmp_path / "no-such-dir" / "utahd.pid")
    lifecycle.write_pidfile()  # must not raise


# -- verified hard exit -------------------------------------------------------

def test_arm_hard_exit_returns_a_cancellable_daemon_timer():
    timer = lifecycle.arm_hard_exit(60.0)
    try:
        assert timer.daemon is True  # never holds the process open by itself
    finally:
        timer.cancel()


def test_arm_hard_exit_rejects_a_nonpositive_timeout():
    with pytest.raises(ValueError):
        lifecycle.arm_hard_exit(0.0)
    with pytest.raises(ValueError):
        lifecycle.arm_hard_exit(-1.0)


def test_arm_hard_exit_terminates_a_hung_drain_for_real():
    """The zombie-daemon killer, proven end-to-end: a subprocess whose 'drain'
    hangs 30s past a 0.3s deadline must hard-exit with the armed code."""
    code = (
        "from utah.daemon import lifecycle; import time; "
        "lifecycle.arm_hard_exit(0.3, code=7); time.sleep(30)"
    )
    t0 = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "PYTHONPATH": f"{REPO}:{os.environ.get('PYTHONPATH', '')}"},
        timeout=20,
    )
    assert proc.returncode == 7              # the timer fired — not the sleep
    assert time.monotonic() - t0 < 15        # and it fired promptly
