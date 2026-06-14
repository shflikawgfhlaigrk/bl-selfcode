"""Foundation probe internals: each substrate probe is bounded, total, and
honest — and check() keeps its never-raises contract even when an injected
probe blows up (a crashed probe is a red check, not an exception)."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys

from utah import failures, foundation
from tests.fakes import FakeFailureStore


def _script(tmp_path, body: str, name: str = "fake_isready"):
    p = tmp_path / name
    p.write_text(f"#!/bin/sh\n{body}\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


# ── postgres_ready ────────────────────────────────────────────────────────────

def test_postgres_ready_missing_binary_is_false(tmp_path):
    assert foundation.postgres_ready(isready=str(tmp_path / "absent")) is False


def test_postgres_ready_true_on_exit_zero(tmp_path):
    assert foundation.postgres_ready(isready=_script(tmp_path, "exit 0")) is True


def test_postgres_ready_false_on_nonzero_exit(tmp_path):
    assert foundation.postgres_ready(isready=_script(tmp_path, "exit 2")) is False


def test_postgres_ready_hang_is_bounded_and_false(tmp_path):
    slow = _script(tmp_path, "sleep 5")
    assert foundation.postgres_ready(isready=slow, timeout_s=0.3, attempts=1) is False


def test_postgres_ready_retries_past_a_transient_flap(tmp_path):
    """A single flaky pg_isready (timeout/refusal under a load spike) must NOT be
    declared 'postgres_down' — PG that answers on a later attempt reads as UP. This
    is the false-alarm fix: confirm before flipping the substrate red."""
    counter = tmp_path / "n"
    flaky = _script(
        tmp_path,
        f'n=$(cat "{counter}" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "{counter}"; '
        '[ "$n" -ge 2 ] && exit 0 || exit 2',
    )
    # first attempt exits non-zero, second exits 0 → UP (retry_sleep=0 keeps it instant)
    assert foundation.postgres_ready(isready=flaky, attempts=3, retry_sleep=0) is True


def test_postgres_ready_all_attempts_fail_is_false(tmp_path):
    """A real outage fails every retry — still caught, not masked by the retry."""
    assert foundation.postgres_ready(
        isready=_script(tmp_path, "exit 2"), attempts=3, retry_sleep=0
    ) is False


# ── supervisor_alive ──────────────────────────────────────────────────────────

def test_supervisor_alive_missing_pidfile_is_false(tmp_path):
    assert foundation.supervisor_alive(pid_path=str(tmp_path / "nope.pid")) is False


def test_supervisor_alive_garbage_pidfile_is_false(tmp_path):
    p = tmp_path / "sup.pid"
    p.write_text("not-a-pid")
    assert foundation.supervisor_alive(pid_path=str(p)) is False


def test_supervisor_alive_pid_zero_is_false(tmp_path):
    """pid 0 means 'this process group' to os.kill — a zeroed pidfile must NOT
    read as a live supervisor."""
    p = tmp_path / "sup.pid"
    p.write_text("0")
    assert foundation.supervisor_alive(pid_path=str(p)) is False


def test_supervisor_alive_negative_pid_is_false(tmp_path):
    p = tmp_path / "sup.pid"
    p.write_text("-1")  # os.kill(-1, 0) probes EVERY process — must be rejected
    assert foundation.supervisor_alive(pid_path=str(p)) is False


def test_supervisor_alive_dead_pid_is_false(tmp_path):
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)  # reaped: its pid is now free (and not promptly reused)
    p = tmp_path / "sup.pid"
    p.write_text(str(proc.pid))
    assert foundation.supervisor_alive(pid_path=str(p)) is False


def test_supervisor_alive_live_pid_is_true(tmp_path):
    p = tmp_path / "sup.pid"
    p.write_text(str(os.getpid()))
    assert foundation.supervisor_alive(pid_path=str(p)) is True


# ── daemon_ping ───────────────────────────────────────────────────────────────

def test_daemon_ping_uses_injected_fn():
    assert foundation.daemon_ping(ping_fn=lambda: True) is True
    assert foundation.daemon_ping(ping_fn=lambda: False) is False


def test_daemon_ping_injected_fn_raising_is_false():
    def boom():
        raise ConnectionRefusedError("socket gone")

    assert foundation.daemon_ping(ping_fn=boom) is False


# ── check() never-raises contract ─────────────────────────────────────────────

def test_check_survives_a_raising_probe(monkeypatch):
    """Docstring contract: 'Never raises.' A probe that blows up is a red check
    plus a recorded anomaly — not an exception into the cron."""
    store = FakeFailureStore()
    failures.set_store(store)
    monkeypatch.setattr("utah.secrets_sync.sync_all", lambda **k: {})
    monkeypatch.setattr("utah.operator.repair_substrate", lambda **k: {"repaired": False})
    monkeypatch.setattr("utah.operator.repair_tailserve", lambda **k: {"repaired": False})

    def boom():
        raise RuntimeError("probe exploded")

    r = foundation.check(postgres_fn=boom, supervisor_fn=lambda: True, ping_fn=lambda: True)
    assert r["ok"] is False and r["state"] == "red"
    assert r["checks"]["postgres"] is False
    assert "postgres_down" in r["anomalies"]
    assert any(row[2] == "postgres_down" for row in store.rows)


def test_check_red_path_attempts_substrate_repair(monkeypatch):
    failures.set_store(FakeFailureStore())
    monkeypatch.setattr("utah.secrets_sync.sync_all", lambda **k: {})
    calls = {"substrate": 0, "tailserve": 0}
    monkeypatch.setattr(
        "utah.operator.repair_substrate",
        lambda **k: calls.__setitem__("substrate", calls["substrate"] + 1) or {"ok": True},
    )
    monkeypatch.setattr(
        "utah.operator.repair_tailserve",
        lambda **k: calls.__setitem__("tailserve", calls["tailserve"] + 1) or {"ok": True},
    )
    r = foundation.check(
        postgres_fn=lambda: False, supervisor_fn=lambda: True, ping_fn=lambda: True
    )
    assert r["ok"] is False
    assert calls["substrate"] == 1 and calls["tailserve"] == 1
    assert r["operator"]["substrate"] == {"ok": True}


# ── read_status / _is_daemon_process ─────────────────────────────────────────

def test_read_status_round_trips_a_valid_snapshot(tmp_path):
    snap = {"ok": True, "state": "green", "checks": {"postgres": True}}
    p = tmp_path / "foundation.json"
    p.write_text(json.dumps(snap))
    assert foundation.read_status(path=str(p)) == snap


def test_is_daemon_process_matches_only_our_own_pid(tmp_path, monkeypatch):
    pidfile = tmp_path / "utahd.pid"
    monkeypatch.setattr(foundation.runtime, "PID_PATH", pidfile)
    assert foundation._is_daemon_process() is False  # no pidfile → standalone cron
    pidfile.write_text(str(os.getpid()))
    assert foundation._is_daemon_process() is True
    pidfile.write_text(str(os.getpid() + 1))
    assert foundation._is_daemon_process() is False
