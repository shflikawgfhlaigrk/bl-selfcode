"""Maintenance boundary hardening: run() promises 'never raises', so a consolidation
report with a wrong shape (missing/garbage attributes) must be documented as a
failure and returned as ok=False — not blow up the nightly cron with AttributeError."""
from __future__ import annotations

from types import SimpleNamespace

from utah import failures, maintenance
from tests.fakes import FakeFailureStore


def _report(**kw):
    base = dict(turns_seen=0, facts_promoted=0, facts_skipped=0,
                brain_failures=0, archived=0)
    base.update(kw)
    return SimpleNamespace(**base)


def test_report_missing_attributes_documented_not_fatal():
    store = FakeFailureStore()
    failures.set_store(store)
    r = maintenance.run(consolidate_fn=lambda: object())   # no count attrs at all
    assert r["ok"] is False
    assert "malformed" in r["error"]
    assert any(row[2] == "malformed_report" for row in store.rows)


def test_report_with_non_numeric_counts_documented_not_fatal():
    store = FakeFailureStore()
    failures.set_store(store)
    r = maintenance.run(consolidate_fn=lambda: _report(turns_seen="lots"))
    assert r["ok"] is False and "malformed" in r["error"]
    assert any(row[2] == "malformed_report" for row in store.rows)


def test_float_counts_are_coerced_not_rejected():
    """A report carrying numeric-but-float counts still reports cleanly."""
    failures.set_store(FakeFailureStore())
    r = maintenance.run(consolidate_fn=lambda: _report(turns_seen=3.0, archived=2.0))
    assert r == {"ok": True, "turns_seen": 3, "facts_promoted": 0,
                 "skipped": 0, "brain_failures": 0, "archived": 2}


def test_zero_brain_failures_records_nothing():
    store = FakeFailureStore()
    failures.set_store(store)
    r = maintenance.run(consolidate_fn=lambda: _report(turns_seen=2, facts_promoted=2))
    assert r["ok"] is True
    assert store.rows == []          # a clean pass adds no failure-log noise


def test_crash_error_is_truncated_for_the_failure_log():
    """A pathological exception message must not flood the failures table."""
    store = FakeFailureStore()
    failures.set_store(store)

    def boom():
        raise RuntimeError("x" * 10_000)

    r = maintenance.run(consolidate_fn=boom)
    assert r["ok"] is False
    detail = next(row[3] for row in store.rows if row[2] == "consolidate_failed")
    assert len(detail) <= 500
