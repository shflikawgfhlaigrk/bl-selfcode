"""Watchdog capability — Ace's watchdog transitions here (NOT an agent): a health check on
Utah's OWN live state (daemon reachable, governor load, failure count). Genuine anomalies
(daemon down, load critical) are recorded to the failure log so nothing fails silently —
Michael's run-forever rule. status/failure-count are injectable; the detection is pure."""
from __future__ import annotations

from utah import failures, watchdog
from tests.fakes import FakeFailureStore


def test_healthy_system_records_nothing():
    store = FakeFailureStore(); failures.set_store(store)
    r = watchdog.check(
        status_fn=lambda: {"governor": {"load_per_core": 1.2}},
        failure_count_fn=lambda: 3,
    )
    assert r["healthy"] is True and r["daemon_up"] is True
    assert r["anomalies"] == [] and store.rows == []      # healthy => no noise


def test_daemon_down_is_detected_and_documented():
    store = FakeFailureStore(); failures.set_store(store)
    r = watchdog.check(status_fn=lambda: None, failure_count_fn=lambda: 0)
    assert r["healthy"] is False and r["daemon_up"] is False
    assert "daemon_unreachable" in r["anomalies"]
    assert any("daemon_unreachable" in row[2] for row in store.rows)


def test_status_raises_treated_as_down():
    store = FakeFailureStore(); failures.set_store(store)
    def boom():
        raise RuntimeError("socket gone")
    r = watchdog.check(status_fn=boom, failure_count_fn=lambda: 0)
    assert r["daemon_up"] is False
    assert any("daemon_unreachable" in row[2] for row in store.rows)


def test_load_critical_is_documented():
    store = FakeFailureStore(); failures.set_store(store)
    r = watchdog.check(
        status_fn=lambda: {"governor": {"load_per_core": 12.5}},
        failure_count_fn=lambda: 0,
    )
    assert r["healthy"] is False and "load_critical" in r["anomalies"]
    assert any("load_critical" in row[2] for row in store.rows)
