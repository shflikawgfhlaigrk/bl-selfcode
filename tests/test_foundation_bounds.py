"""Foundation probe totality beyond the postgres arm: a non-executable isready
binary, and EVERY probe slot in check() (supervisor, daemon) surviving a raise —
the never-raises contract holds for each arm independently."""
from __future__ import annotations

from utah import failures, foundation
from tests.fakes import FakeFailureStore


def test_postgres_ready_non_executable_binary_is_false(tmp_path):
    p = tmp_path / "isready"
    p.write_text("#!/bin/sh\nexit 0\n")  # present but NOT executable → OSError arm
    assert foundation.postgres_ready(isready=str(p)) is False


def _patched_side_effects(monkeypatch):
    monkeypatch.setattr("utah.secrets_sync.sync_all", lambda **k: {})
    monkeypatch.setattr("utah.operator.repair_substrate", lambda **k: {"repaired": False})
    monkeypatch.setattr("utah.operator.repair_tailserve", lambda **k: {"repaired": False})


def test_check_survives_a_raising_supervisor_probe(monkeypatch):
    store = FakeFailureStore()
    failures.set_store(store)
    _patched_side_effects(monkeypatch)

    def boom():
        raise OSError("pidfile filesystem gone")

    r = foundation.check(postgres_fn=lambda: True, supervisor_fn=boom, ping_fn=lambda: True)
    assert r["ok"] is False and r["checks"]["supervisor"] is False
    assert "supervisor_down" in r["anomalies"]
    assert any(row[2] == "supervisor_down" for row in store.rows)


def test_check_survives_a_raising_daemon_probe(monkeypatch):
    store = FakeFailureStore()
    failures.set_store(store)
    _patched_side_effects(monkeypatch)

    def boom():
        raise ConnectionResetError("socket reset mid-ping")

    r = foundation.check(postgres_fn=lambda: True, supervisor_fn=lambda: True, ping_fn=boom)
    assert r["ok"] is False and r["checks"]["daemon"] is False
    assert "daemon_unreachable" in r["anomalies"]
    assert any(row[2] == "daemon_unreachable" for row in store.rows)


def test_probe_truthiness_is_normalized(monkeypatch):
    """A probe returning a truthy non-bool (a dict, a pid int) must normalize to
    bool — the snapshot is JSON the deck consumes."""
    store = FakeFailureStore()
    failures.set_store(store)
    monkeypatch.setattr("utah.secrets_sync.sync_all", lambda **k: {})
    monkeypatch.setattr("utah.operator.run", lambda **k: {})
    r = foundation.check(
        postgres_fn=lambda: {"up": True},   # truthy dict
        supervisor_fn=lambda: 12345,        # truthy pid
        ping_fn=lambda: True,
    )
    assert r["checks"]["postgres"] is True
    assert r["checks"]["supervisor"] is True
    assert r["ok"] is True
