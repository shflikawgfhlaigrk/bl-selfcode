"""ops/verify_stack.sh — the stack verifier, exercised against a FULLY FAKE stack.

The sweep must ASSERT, not just print: every claim is an OK/FAIL line, the exit
code is the honest signal (0 = all claims held), a wedged psql/ollama can never
hang it (kill-watchdog — macOS has no `timeout`), and the DSN reaches the OLAP
probe via the environment — never interpolated into python source (a quote in
the DSN must arrive intact, not splice code). Fakes only; never the live stack.
"""
from __future__ import annotations

import os
import pathlib
import stat
import subprocess
import time

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "ops" / "verify_stack.sh"


def _exe(path: pathlib.Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


_PY_FAKE = (
    'if [ "${1:-}" = "--version" ]; then echo "Python 3.12.0"; exit 0; fi\n'
    'if [ "${1:-}" = "-c" ]; then\n'
    '  [ -n "${UTAH_OLAP_DSN:-}" ] && echo "olap-dsn:${UTAH_OLAP_DSN}" >> "$CALLS"\n'
    '  echo 4666; exit 0\n'
    'fi\n'
    'echo "{}"\nexit 0\n'   # e.g. invoked on ops/foundation_check.py
)


@pytest.fixture()
def stack(tmp_path):
    """A green sandbox stack: fake repo, fake venvs, fake CLIs on PATH."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    fakebin = tmp_path / "fakebin"
    calls = tmp_path / "calls.log"
    calls.parent.mkdir(parents=True, exist_ok=True)
    calls.touch()

    # repo skeleton the script inspects
    (repo / "utah" / "store").mkdir(parents=True)
    (repo / "utah" / "config.py").write_text('DB_DSN = "host=/tmp port=5433 dbname=utah"\n')
    (repo / "utah" / "store" / "__init__.py").write_text("# Utah OLAP tier\n")
    (repo / "utah" / "wired.py").write_text("from utah.store import olap\n")
    (repo / "ops").mkdir()
    (repo / "ops" / "foundation_check.py").write_text("print('{}')\n")

    # fake venvs + piper
    _exe(repo / ".venv" / "bin" / "python", _PY_FAKE)
    _exe(home / ".utah" / "venv" / "bin" / "python", _PY_FAKE)
    _exe(home / ".utah" / "venv" / "bin" / "piper", "exit 0\n")
    _exe(repo / ".venv" / "bin" / "piper", "exit 0\n")

    # fake CLIs ahead of the real PATH
    _exe(fakebin / "psql", 'echo "psql $*" >> "$CALLS"\necho 42\nexit 0\n')
    _exe(fakebin / "pg_isready", "exit 0\n")
    _exe(fakebin / "duckdb", 'echo "v1.2.3 deadbeef"\nexit 0\n')
    _exe(fakebin / "ollama", 'echo "llama3.2:3b  2.0 GB"\nexit 0\n')
    _exe(fakebin / "launchctl", 'echo "123 0 com.utah.supervisor"\nexit 0\n')
    for b in ("whisper-cli", "ffmpeg", "ffprobe"):
        _exe(fakebin / b, "exit 0\n")

    (home / ".utah" / "models").mkdir(parents=True)
    (home / ".utah" / "run").mkdir(parents=True)

    env = dict(os.environ)
    env.update({
        "HOME": str(home),
        "UTAH_REPO": str(repo),
        "UTAH_PY": str(home / ".utah" / "venv" / "bin" / "python"),
        "UTAH_DEV_PY": str(repo / ".venv" / "bin" / "python"),
        "PG_ISREADY": str(fakebin / "pg_isready"),
        "UTAH_DSN": "host=/tmp port=5433 dbname=utah",
        "UTAH_STACK_TIMEOUT": "5",
        "PATH": f"{fakebin}:{env['PATH']}",
        "CALLS": str(calls),
    })
    return env, {"home": home, "repo": repo, "bin": fakebin, "calls": calls}


def _run(env, timeout=60):
    return subprocess.run(["/bin/bash", str(SCRIPT)], env=env,
                          capture_output=True, text=True, timeout=timeout)


def test_script_parses_under_bash_n():
    p = subprocess.run(["/bin/bash", "-n", str(SCRIPT)],
                       capture_output=True, text=True, timeout=15)
    assert p.returncode == 0, p.stderr


def test_all_green_stack_exits_zero(stack):
    env, _paths = stack
    p = _run(env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "all checks passed" in p.stdout
    assert "FAILED" not in p.stdout


def test_postgres_down_is_a_counted_fail_and_nonzero_exit(stack):
    env, paths = stack
    _exe(paths["bin"] / "pg_isready", "exit 2\n")
    p = _run(env)
    assert p.returncode == 1, "a failed claim must surface in the exit code"
    assert "postgres not accepting" in p.stdout
    assert "FAILED" in p.stdout and "all checks passed" not in p.stdout


def test_missing_runtime_venv_is_a_fail(stack):
    env, paths = stack
    env["UTAH_PY"] = str(paths["home"] / "nonexistent" / "python")
    p = _run(env)
    assert p.returncode == 1
    assert "runtime venv missing" in p.stdout


def test_wedged_psql_is_bounded_not_a_hang(stack):
    env, paths = stack
    _exe(paths["bin"] / "psql", "exec sleep 60\n")
    env["UTAH_STACK_TIMEOUT"] = "1"
    t0 = time.monotonic()
    p = _run(env, timeout=120)
    elapsed = time.monotonic() - t0
    assert elapsed < 60, f"wedged psql must be watchdog-killed; sweep took {elapsed:.0f}s"
    assert "<missing-or-wedged>" in p.stdout, "killed queries must read as unknown, not green"


def test_dsn_reaches_olap_via_env_never_spliced_into_code(stack):
    env, paths = stack
    evil = "host=/tmp dbname=utah' ; print('pwned') ; '"
    env["UTAH_DSN"] = evil
    p = _run(env)
    logged = paths["calls"].read_text()
    assert f"olap-dsn:{evil}" in logged, \
        "the DSN must arrive via the environment, byte-for-byte intact"
    assert "pwned" not in p.stdout
    assert "olap.query leads count: 4666" in p.stdout


def test_foundation_probe_red_is_a_fail(stack):
    env, paths = stack
    # runtime python exits 1 when handed a script path (red foundation)
    _exe(paths["home"] / ".utah" / "venv" / "bin" / "python",
         'if [ "${1:-}" = "--version" ]; then echo "Python 3.12.0"; exit 0; fi\n'
         'if [ "${1:-}" = "-c" ]; then echo 4666; exit 0; fi\n'
         'echo "{\\"ok\\": false}"\nexit 1\n')
    p = _run(env)
    assert p.returncode == 1
    assert "foundation probe red" in p.stdout
