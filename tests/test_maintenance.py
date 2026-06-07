"""Maintenance capability — Ace's nightly_maintenance transitions here (NOT an agent):
run memory consolidation (promote turns->durable facts) + decay (archive faded rows) to
keep memory healthy. brain-extraction failures are documented to the failure log. The
consolidate call is injectable; the orchestration + failure-tracking are tested."""
from __future__ import annotations

from types import SimpleNamespace

from utah import failures, maintenance
from tests.fakes import FakeFailureStore


def _report(**kw):
    base = dict(turns_seen=0, facts_promoted=0, facts_skipped=0, brain_failures=0, archived=0)
    base.update(kw)
    return SimpleNamespace(**base)


def test_run_reports_counts():
    failures.set_store(FakeFailureStore())
    r = maintenance.run(consolidate_fn=lambda: _report(
        turns_seen=5, facts_promoted=3, skipped=2, archived=7))
    assert r["ok"] is True
    assert r["facts_promoted"] == 3 and r["archived"] == 7 and r["turns_seen"] == 5


def test_brain_failures_are_documented():
    store = FakeFailureStore(); failures.set_store(store)
    maintenance.run(consolidate_fn=lambda: _report(turns_seen=4, brain_failures=4))
    assert any("brain_failures" in row[2] for row in store.rows)


def test_consolidate_crash_is_documented_not_fatal():
    store = FakeFailureStore(); failures.set_store(store)
    def boom():
        raise RuntimeError("postgres gone")
    r = maintenance.run(consolidate_fn=boom)
    assert r["ok"] is False and "error" in r
    assert any("consolidate_failed" in row[2] for row in store.rows)
    assert any("postgres gone" in row[3] for row in store.rows)
