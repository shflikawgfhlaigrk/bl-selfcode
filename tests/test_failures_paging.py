"""failures.record side-channels: detail truncation, type coercion, the
critical-kind paging hook, the Discord audit feed — each one swallowed when it
breaks (recording a failure must NEVER cause one) — and the count/recent
boundaries degrading to honest zeros/empties."""
from __future__ import annotations

import pytest

from utah import config, failures
from tests.fakes import FakeFailureStore


@pytest.fixture(autouse=True)
def _fresh_store():
    store = FakeFailureStore()
    failures.set_store(store)
    yield store
    failures.set_store(None)


def test_detail_is_truncated_to_max_detail(_fresh_store):
    failures.record("src", "kind", "x" * (failures.MAX_DETAIL + 500))
    (_, _, _, detail), = _fresh_store.rows
    assert len(detail) == failures.MAX_DETAIL


def test_non_string_inputs_are_coerced(_fresh_store):
    failures.record(123, ("tuple", "kind"), {"detail": True})  # type: ignore[arg-type]
    (_, source, kind, detail), = _fresh_store.rows
    assert source == "123" and "tuple" in kind and "detail" in detail


def test_critical_kind_pages_the_phone_once(monkeypatch, _fresh_store):
    paged: list[tuple] = []
    monkeypatch.setattr(
        "utah.alerts.critical_async", lambda src, detail, key=None: paged.append((src, detail, key))
    )
    kind = next(iter(config.CRITICAL_FAILURE_KINDS))
    failures.record("daemon", kind, "the daemon fell over")
    assert paged == [("daemon", "the daemon fell over", f"daemon/{kind}")]
    assert len(_fresh_store.rows) == 1  # still recorded durably


def test_non_critical_kind_never_pages(monkeypatch):
    paged: list[tuple] = []
    monkeypatch.setattr("utah.alerts.critical_async", lambda *a, **k: paged.append(a))
    assert "definitely_not_critical" not in config.CRITICAL_FAILURE_KINDS
    failures.record("daemon", "definitely_not_critical", "meh")
    assert paged == []


def test_paging_hook_blowing_up_is_swallowed(monkeypatch, _fresh_store):
    def boom(*a, **k):
        raise RuntimeError("pushover down")

    monkeypatch.setattr("utah.alerts.critical_async", boom)
    kind = next(iter(config.CRITICAL_FAILURE_KINDS))
    failures.record("daemon", kind, "still must not raise")
    assert len(_fresh_store.rows) == 1  # the durable write survived the page failure


def test_discord_hook_blowing_up_is_swallowed(monkeypatch, _fresh_store):
    def boom(*a, **k):
        raise RuntimeError("webhook 404")

    monkeypatch.setattr("utah.integrations.discord_feed.feed_audit", boom)
    failures.record("src", "kind", "detail")
    assert len(_fresh_store.rows) == 1


def test_count_returns_real_count():
    failures.record("a", "b", "c")
    failures.record("a", "b", "d")
    assert failures.count() == 2


def test_count_degrades_to_zero_when_store_breaks():
    class BrokenCount(FakeFailureStore):
        def count(self):
            raise RuntimeError("pg gone")

    failures.set_store(BrokenCount())
    assert failures.count() == 0


def test_recent_degrades_to_empty_when_store_breaks():
    class BrokenRecent(FakeFailureStore):
        def recent(self, limit):
            raise RuntimeError("pg gone")

    failures.set_store(BrokenRecent())
    assert failures.recent(10) == []  # real-or-empty: never fabricated, never raises


def test_recent_limit_is_clamped_to_sane_bounds():
    captured: list[int] = []

    class Capturing(FakeFailureStore):
        def recent(self, limit):
            captured.append(limit)
            return []

    failures.set_store(Capturing())
    failures.recent(0)          # a zero/negative ask is still one row, not an error
    failures.recent(10**9)      # a deck bug can't turn into a full-table scan
    assert captured[0] >= 1
    assert captured[1] <= 1000
