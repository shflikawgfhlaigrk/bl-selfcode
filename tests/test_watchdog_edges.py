"""Watchdog never-raises edges: malformed status payloads (non-dict, junk load
values), the exact load threshold boundary, a throwing failure-count probe, and a
failure store that explodes — check() must stay a calm, honest snapshot through
all of it (it is wired straight into the deck's watchdog panel)."""
from __future__ import annotations

from tests.fakes import FakeFailureStore
from utah import failures, watchdog


def _with_store():
    store = FakeFailureStore()
    failures.set_store(store)
    return store


def test_load_exactly_at_threshold_is_healthy():
    _with_store()
    r = watchdog.check(
        status_fn=lambda: {"governor": {"load_per_core": watchdog.LOAD_CRITICAL}},
        failure_count_fn=lambda: 0,
    )
    assert r["healthy"] is True and r["anomalies"] == []   # strictly OVER trips, AT does not


def test_missing_governor_block_reports_unknown_load_not_a_crash():
    _with_store()
    r = watchdog.check(status_fn=lambda: {"uptime": 5}, failure_count_fn=lambda: 0)
    assert r["daemon_up"] is True
    assert r["load_per_core"] is None
    assert r["healthy"] is True                            # unknown load ≠ anomaly


def test_non_dict_status_is_treated_as_unreachable():
    """A daemon that answers garbage is NOT healthy — and .get() on a string must
    not blow up the watchdog itself."""
    store = _with_store()
    r = watchdog.check(status_fn=lambda: "wedged", failure_count_fn=lambda: 0)
    assert r["healthy"] is False and r["daemon_up"] is False
    assert "daemon_unreachable" in r["anomalies"]
    assert any("daemon_unreachable" in row[2] for row in store.rows)


def test_junk_load_value_does_not_crash_the_check():
    _with_store()
    r = watchdog.check(
        status_fn=lambda: {"governor": {"load_per_core": "very high"}},
        failure_count_fn=lambda: 0,
    )
    assert r["daemon_up"] is True
    assert r["load_per_core"] is None                      # junk → unknown, not a TypeError


def test_failure_count_probe_raising_yields_none_count():
    _with_store()

    def boom() -> int:
        raise RuntimeError("pg down")

    r = watchdog.check(status_fn=lambda: {"governor": {"load_per_core": 0.5}},
                       failure_count_fn=boom)
    assert r["failures"] is None
    assert r["healthy"] is True                            # count probe ≠ health verdict


def test_exploding_failure_store_never_breaks_the_snapshot():
    store = _with_store()
    store.fail = True                                      # insert() raises
    r = watchdog.check(status_fn=lambda: None, failure_count_fn=lambda: 0)
    assert r["healthy"] is False
    assert "daemon_unreachable" in r["anomalies"]          # the verdict still lands


def test_healthy_snapshot_carries_the_real_failure_count():
    _with_store()
    r = watchdog.check(status_fn=lambda: {"governor": {"load_per_core": 0.2}},
                       failure_count_fn=lambda: 7)
    assert r == {"healthy": True, "daemon_up": True, "load_per_core": 0.2,
                 "failures": 7, "anomalies": []}           # exact deck-panel shape


def test_load_critical_threshold_is_env_tunable(monkeypatch):
    import importlib

    monkeypatch.setenv("UTAH_WATCHDOG_LOAD_CRITICAL", "3.5")
    importlib.reload(watchdog)
    try:
        assert watchdog.LOAD_CRITICAL == 3.5
        _with_store()
        r = watchdog.check(
            status_fn=lambda: {"governor": {"load_per_core": 2.0}},
            failure_count_fn=lambda: 0,
        )
        assert r["healthy"] is True                        # 2.0 under the raised bar
    finally:
        monkeypatch.undo()
        importlib.reload(watchdog)                         # restore the live default
