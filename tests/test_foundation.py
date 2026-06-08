"""Foundation probe — substrate health (Postgres, supervisor, daemon ping)."""
from __future__ import annotations

from utah import failures, foundation
from tests.fakes import FakeFailureStore


def test_all_green_records_nothing(monkeypatch):
    store = FakeFailureStore()
    failures.set_store(store)
    monkeypatch.setattr(
        "utah.operator.run",
        lambda **k: {"integrations": {"mail": {"ok": True}}},
    )
    monkeypatch.setattr("utah.secrets_sync.sync_all", lambda **k: {"still_missing": []})
    r = foundation.check(
        postgres_fn=lambda: True,
        supervisor_fn=lambda: True,
        ping_fn=lambda: True,
    )
    assert r["ok"] is True and r["state"] == "green"
    assert r["anomalies"] == [] and store.rows == []


def test_postgres_down_is_documented():
    store = FakeFailureStore()
    failures.set_store(store)
    r = foundation.check(
        postgres_fn=lambda: False,
        supervisor_fn=lambda: True,
        ping_fn=lambda: True,
    )
    assert r["ok"] is False and "postgres_down" in r["anomalies"]
    assert any(row[2] == "postgres_down" for row in store.rows)


def test_supervisor_down_is_documented():
    store = FakeFailureStore()
    failures.set_store(store)
    r = foundation.check(
        postgres_fn=lambda: True,
        supervisor_fn=lambda: False,
        ping_fn=lambda: True,
    )
    assert "supervisor_down" in r["anomalies"]
    assert any(row[2] == "supervisor_down" for row in store.rows)


def test_daemon_down_is_documented():
    store = FakeFailureStore()
    failures.set_store(store)
    r = foundation.check(
        postgres_fn=lambda: True,
        supervisor_fn=lambda: True,
        ping_fn=lambda: False,
    )
    assert "daemon_unreachable" in r["anomalies"]
    assert any(row[2] == "daemon_unreachable" for row in store.rows)


def test_gate_cron_green_returns_none():
    store = FakeFailureStore()
    failures.set_store(store)
    assert foundation.gate_cron("leads", status={"ok": True, "state": "green"}) is None
    assert store.rows == []


def test_gate_cron_red_returns_skip_and_records():
    store = FakeFailureStore()
    failures.set_store(store)
    skip = foundation.gate_cron(
        "marketer",
        status={"ok": False, "state": "red", "anomalies": ["postgres_down"],
                "checks": {"postgres": False}},
    )
    assert skip["status"] == "substrate_red"
    assert skip["capability"] == "marketer"
    assert skip["anomalies"] == ["postgres_down"]
    assert any(row[2] == "cron_gated" for row in store.rows)


def test_gate_cron_missing_status_is_red():
    store = FakeFailureStore()
    failures.set_store(store)
    skip = foundation.gate_cron("leads", status=None)
    assert skip is not None and skip["status"] == "substrate_red"
    assert "foundation_unknown" in skip["anomalies"]
