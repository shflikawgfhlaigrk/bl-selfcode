"""Failure log + silent-failure sink: loud observability that NEVER itself fails.
record()/record_silent() swallow every error (de-silencing a failure must not
cause one); recent() feeds the deck AUDIT LEDGER panel (most-recent-first, capped).
"""
from __future__ import annotations

import pytest

from utah import failures
from tests.fakes import FakeFailureStore


@pytest.fixture(autouse=True)
def _restore_failure_store():
    yield
    failures.set_store(None)


def test_record_then_recent_returns_it():
    failures.set_store(FakeFailureStore())
    failures.record("daemon", "brain_unavailable", "cli gone")
    rows = failures.recent(10)
    assert len(rows) == 1
    assert rows[0].source == "daemon"
    assert rows[0].kind == "brain_unavailable"
    assert "cli gone" in rows[0].detail


def test_record_does_not_cascade_via_alert_side_effects():
    """A failure recorded BY record()'s own alert/mirror side-effects must not fan back
    into more recorded failures. Regression for the 2026-06-14 cascade: one
    record('daemon','test') produced 249 rows (discord mirror → failed post →
    webhook_post_failed → ...), poisoning the feed and turning every count-based test red
    (which silently stalled the selfcode gate for ~28h). A re-entrant store proves the
    guard: its insert re-enters record(), which must persist-only and never loop."""
    store = FakeFailureStore()

    class ReentrantStore:
        """insert() re-enters failures.record once, simulating a side-effect that records."""
        def __init__(self):
            self.depth = 0
            self.inserts = 0
        def init_schema(self):
            pass
        def insert(self, source, kind, detail):
            self.inserts += 1
            self.depth += 1
            if self.depth == 1:
                failures.record("discord", "webhook_post_failed", "boom")  # re-entry
            self.depth -= 1
        def recent(self, limit):
            return []
        def count(self):
            return self.inserts

    rs = ReentrantStore()
    failures.set_store(rs)
    failures.record("daemon", "test", "x")          # must terminate, not cascade
    # Exactly two inserts: the outer record + the single re-entrant one. No runaway.
    assert rs.inserts == 2


def test_record_silent_records_a_silent_kind():
    failures.set_store(FakeFailureStore())
    failures.record_silent("dispatch", "swallowed except-pass at X")
    rows = failures.recent(10)
    assert len(rows) == 1
    assert rows[0].kind == "silent"
    assert "except-pass" in rows[0].detail


def test_record_never_raises_even_when_store_is_down():
    store = FakeFailureStore()
    store.fail = True
    failures.set_store(store)
    # both sinks must swallow — recording a failure must never cause one
    failures.record("x", "y", "z")
    failures.record_silent("x", "z")


def test_record_never_raises_when_no_store_configured(monkeypatch):
    # if the backend can't even be constructed, recording is still a no-op
    failures.set_store(None)
    monkeypatch.setattr(failures, "get_store", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    failures.record("x", "y", "z")  # must not raise


def test_recent_is_most_recent_first_and_capped():
    failures.set_store(FakeFailureStore())
    for i in range(5):
        failures.record("s", f"k{i}", f"d{i}")
    rows = failures.recent(3)
    assert len(rows) == 3
    assert rows[0].kind == "k4"  # newest first
    assert rows[-1].kind == "k2"


def test_recent_empty_when_no_failures():
    failures.set_store(FakeFailureStore())
    assert failures.recent(10) == []
