"""utah_pg.sh — the Postgres self-heal guard, exercised against FAKE pg binaries.

The guard's contract: no-op when PG accepts, clear only a truly-stale pidfile,
start with a bounded wait, log honestly, and exit nonzero when the start FAILS
(launchd's StartInterval retries; the exit code is the honest signal). Every test
runs the real script under bash with an injected PGBIN dir — never the live cluster.
"""
from __future__ import annotations

import os
import pathlib
import stat
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "ops" / "bin" / "utah_pg.sh"


def _write_exe(path: pathlib.Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture()
def env(tmp_path):
    """A sandbox: fake PGBIN, empty PGDATA, logs under tmp. Returns (env, paths)."""
    pgbin = tmp_path / "pgbin"
    pgbin.mkdir()
    pgdata = tmp_path / "pgdata"
    pgdata.mkdir()
    calls = tmp_path / "calls.log"
    calls.touch()
    e = dict(os.environ)
    e.update({
        "HOME": str(tmp_path),
        "UTAH_PGBIN": str(pgbin),
        "UTAH_PGDATA": str(pgdata),
        "UTAH_PG_GUARD_LOG": str(tmp_path / "guard.log"),
        "UTAH_PG_SERVER_LOG": str(tmp_path / "pg.log"),
        "UTAH_PG_PORT": "59999",
        "UTAH_PG_SOCKDIR": str(tmp_path),
        "CALLS": str(calls),
    })
    return e, {"pgbin": pgbin, "pgdata": pgdata, "calls": calls, "tmp": tmp_path}


def _run(e):
    return subprocess.run(["/bin/bash", str(SCRIPT)], env=e,
                          capture_output=True, text=True, timeout=30)


def test_script_parses_under_bash_n():
    p = subprocess.run(["/bin/bash", "-n", str(SCRIPT)],
                       capture_output=True, text=True, timeout=15)
    assert p.returncode == 0, p.stderr


def test_noop_when_already_accepting(env):
    e, paths = env
    _write_exe(paths["pgbin"] / "pg_isready", 'echo "isready $*" >> "$CALLS"\nexit 0\n')
    _write_exe(paths["pgbin"] / "pg_ctl", 'echo "pg_ctl $*" >> "$CALLS"\nexit 0\n')
    p = _run(e)
    assert p.returncode == 0
    calls = paths["calls"].read_text()
    assert "isready" in calls and "pg_ctl" not in calls


def test_starts_and_logs_when_down(env):
    e, paths = env
    _write_exe(paths["pgbin"] / "pg_isready", "exit 2\n")
    _write_exe(paths["pgbin"] / "pg_ctl", 'echo "pg_ctl $*" >> "$CALLS"\nexit 0\n')
    p = _run(e)
    assert p.returncode == 0
    calls = paths["calls"].read_text()
    assert "start" in calls and str(paths["pgdata"]) in calls
    assert "-t 30" in calls, "start wait must stay bounded"
    assert "started Utah PG" in (paths["tmp"] / "guard.log").read_text()


def test_failed_start_logs_and_exits_nonzero(env):
    e, paths = env
    _write_exe(paths["pgbin"] / "pg_isready", "exit 2\n")
    _write_exe(paths["pgbin"] / "pg_ctl", "exit 1\n")
    p = _run(e)
    assert p.returncode != 0, "a failed self-heal must not report success"
    assert "FAILED" in (paths["tmp"] / "guard.log").read_text()


def test_missing_binaries_is_a_logged_gate_not_a_crash(env):
    e, paths = env
    p = _run(e)  # PGBIN dir is empty
    assert p.returncode == 0
    assert "missing" in (paths["tmp"] / "guard.log").read_text()


def test_stale_pidfile_is_cleared_before_start(env):
    e, paths = env
    _write_exe(paths["pgbin"] / "pg_isready", "exit 2\n")
    _write_exe(paths["pgbin"] / "pg_ctl", "exit 0\n")
    pidfile = paths["pgdata"] / "postmaster.pid"
    pidfile.write_text("999999\n")  # beyond macOS pid range -> definitely dead
    _run(e)
    assert not pidfile.exists()
    assert "stale postmaster.pid" in (paths["tmp"] / "guard.log").read_text()


def test_live_pidfile_is_left_alone(env):
    e, paths = env
    _write_exe(paths["pgbin"] / "pg_isready", "exit 2\n")
    _write_exe(paths["pgbin"] / "pg_ctl", "exit 0\n")
    pidfile = paths["pgdata"] / "postmaster.pid"
    pidfile.write_text(f"{os.getpid()}\n")  # this very test process: alive
    _run(e)
    assert pidfile.exists(), "a live postmaster's pidfile must never be removed"


def test_concurrent_run_is_skipped_by_the_lock(env):
    e, paths = env
    _write_exe(paths["pgbin"] / "pg_isready", "exit 2\n")
    _write_exe(paths["pgbin"] / "pg_ctl", 'echo "pg_ctl $*" >> "$CALLS"\nexit 0\n')
    lock = paths["tmp"] / ".utah" / "run" / "utah_pg.lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text(f"{os.getpid()}\n")  # holder alive -> skip
    p = _run(e)
    assert p.returncode == 0
    assert "pg_ctl" not in paths["calls"].read_text()


def test_stale_lock_from_dead_holder_is_broken(env):
    e, paths = env
    _write_exe(paths["pgbin"] / "pg_isready", "exit 2\n")
    _write_exe(paths["pgbin"] / "pg_ctl", 'echo "pg_ctl $*" >> "$CALLS"\nexit 0\n')
    lock = paths["tmp"] / ".utah" / "run" / "utah_pg.lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text("999999\n")  # holder long gone
    p = _run(e)
    assert p.returncode == 0
    assert "pg_ctl" in paths["calls"].read_text(), "stale lock must not wedge the guard"
