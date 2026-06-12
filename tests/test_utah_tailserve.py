"""utah_tailserve.sh — the tailnet self-heal guard, exercised against a FAKE
Tailscale CLI. Contract: no-op when :8765 already proxies Utah, re-assert (and log)
when clobbered, honest nonzero exit + log line when the re-assert fails, silent
gate when Tailscale isn't installed, and BOUNDED — a wedged tailscaled must never
hang the guard (the 2-minute launchd tick would pile up hung copies forever).
"""
from __future__ import annotations

import os
import pathlib
import stat
import subprocess
import time

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "ops" / "bin" / "utah_tailserve.sh"


def _write_ts(path: pathlib.Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture()
def env(tmp_path):
    ts = tmp_path / "tailscale"
    calls = tmp_path / "calls.log"
    calls.touch()
    e = dict(os.environ)
    e.update({
        "HOME": str(tmp_path),
        "UTAH_TS_BIN": str(ts),
        "UTAH_TAILSERVE_LOG": str(tmp_path / "tailserve.log"),
        "UTAH_TS_TIMEOUT": "2",
        "CALLS": str(calls),
    })
    return e, {"ts": ts, "calls": calls, "log": tmp_path / "tailserve.log"}


def _run(e, timeout=30):
    return subprocess.run(["/bin/bash", str(SCRIPT)], env=e,
                          capture_output=True, text=True, timeout=timeout)


def test_script_parses_under_bash_n():
    p = subprocess.run(["/bin/bash", "-n", str(SCRIPT)],
                       capture_output=True, text=True, timeout=15)
    assert p.returncode == 0, p.stderr


def test_noop_when_already_hooked_to_utah(env):
    e, paths = env
    _write_ts(paths["ts"], 'echo "ts $*" >> "$CALLS"\n'
              'if [ "$1 $2" = "serve status" ]; then\n'
              '  echo "https://host:8765 (tailnet only)"\n'
              '  echo "|-- proxy http://127.0.0.1:8766"\n'
              '  exit 0\nfi\nexit 0\n')
    p = _run(e)
    assert p.returncode == 0
    assert "serve --bg" not in paths["calls"].read_text()


def test_reasserts_and_logs_when_clobbered(env):
    e, paths = env
    _write_ts(paths["ts"], 'echo "ts $*" >> "$CALLS"\n'
              'if [ "$1 $2" = "serve status" ]; then\n'
              '  echo "|-- proxy http://127.0.0.1:8765"\n'  # old-Ace squatting
              '  exit 0\nfi\nexit 0\n')
    p = _run(e)
    assert p.returncode == 0
    calls = paths["calls"].read_text()
    assert "serve --bg --http=8765 http://127.0.0.1:8766" in calls
    assert "re-asserted" in paths["log"].read_text()


def test_dots_in_target_are_not_regex_wildcards(env):
    e, paths = env
    # "127a0b0c1:8766" would match the old unescaped regex "127.0.0.1:8766".
    _write_ts(paths["ts"], 'echo "ts $*" >> "$CALLS"\n'
              'if [ "$1 $2" = "serve status" ]; then\n'
              '  echo "|-- proxy http://127a0b0c1:8766"\n'
              '  exit 0\nfi\nexit 0\n')
    _run(e)
    assert "serve --bg" in paths["calls"].read_text(), \
        "a lookalike target must NOT count as already-hooked"


def test_failed_reassert_logs_and_exits_nonzero(env):
    e, paths = env
    _write_ts(paths["ts"], 'if [ "$1 $2" = "serve status" ]; then exit 0; fi\nexit 1\n')
    p = _run(e)
    assert p.returncode != 0, "a failed self-heal must not report success"
    assert "FAILED" in paths["log"].read_text()


def test_missing_tailscale_is_a_quiet_gate(env):
    e, _paths = env  # UTAH_TS_BIN points at a nonexistent file
    p = _run(e)
    assert p.returncode == 0


def test_wedged_tailscaled_cannot_hang_the_guard(env):
    e, paths = env
    # exec = the CLI itself wedges (one process, like the real binary blocking
    # on tailscaled's socket) — the watchdog must SIGKILL it and move on.
    _write_ts(paths["ts"], 'exec sleep 30\n')
    t0 = time.monotonic()
    p = _run(e, timeout=25)
    elapsed = time.monotonic() - t0
    assert elapsed < 15, f"guard must be bounded; took {elapsed:.1f}s"
    assert p.returncode != 0
    assert "FAILED" in paths["log"].read_text()
