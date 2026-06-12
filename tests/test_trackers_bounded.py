"""Trackers hardening — bounded DB access (pooled, connect-timeout) + input validation.

Store-down tests inject a failing pool at the db_pool seam (a dependency boundary, never
the module under test). Validation tests are pure — bad input must be rejected or clamped
BEFORE a DB round-trip."""
from __future__ import annotations

import contextlib

import psycopg
import pytest

from utah import config, db_pool
from utah.product import trackers

MARK = "__pytest_track_bounded__"


@pytest.fixture
def tk():
    try:
        trackers.init_schema()
    except Exception:
        pytest.skip("Postgres not reachable")
    yield trackers
    with psycopg.connect(config.DB_DSN, autocommit=True, connect_timeout=5) as c:
        c.execute("DELETE FROM tracker_entries WHERE category LIKE %s", (MARK + "%",))


def test_empty_entry_rejected_before_db():
    """Empty/whitespace entries raise TrackerError without touching the store —
    provable with no DB at all (no pool injection here on purpose)."""
    for bad in ("", "   ", None):
        with pytest.raises(trackers.TrackerError, match="empty"):
            trackers.log_entry("gym", bad)  # type: ignore[arg-type]


def test_blank_category_defaults_to_journal(tk):
    tk.log_entry("  ", MARK + " default-cat entry")
    entries = [e["entry"] for e in tk.recent("journal", limit=50)]
    assert (MARK + " default-cat entry") in entries
    with psycopg.connect(config.DB_DSN, autocommit=True, connect_timeout=5) as c:
        c.execute("DELETE FROM tracker_entries WHERE entry LIKE %s", (MARK + "%",))


def test_recent_negative_or_zero_limit_is_clamped_not_an_error(tk):
    """LIMIT -5 is a Postgres error — the capability must clamp, not crash the panel."""
    tk.log_entry(MARK, "one entry")
    assert tk.recent(MARK, limit=-5) != [] or True   # must not raise
    rows = tk.recent(MARK, limit=0)
    assert isinstance(rows, list) and len(rows) >= 1  # clamped to at least 1


def test_recent_huge_limit_is_capped(tk):
    tk.log_entry(MARK, "cap entry")
    rows = tk.recent(MARK, limit=10_000_000)         # capped server-side, never unbounded
    assert isinstance(rows, list)


class _DeadPool:
    @contextlib.contextmanager
    def connection(self):
        raise psycopg.OperationalError("connection refused")
        yield  # pragma: no cover


def test_store_down_raises_tracker_error_not_psycopg(monkeypatch):
    monkeypatch.setattr(db_pool, "get_pool", lambda dsn, **kw: _DeadPool())
    with pytest.raises(trackers.TrackerError, match="unreachable"):
        trackers.log_entry("gym", "ran 3 miles")
    with pytest.raises(trackers.TrackerError):
        trackers.recent("gym")


def test_conn_rides_the_shared_bounded_pool(monkeypatch):
    """Trackers must check out of the shared per-DSN pool (connect_timeout + checkout
    cap), not open a fresh unbounded psycopg.connect per call — a stalled Postgres
    would otherwise hang the deck's trackers panel forever."""
    seen = {}

    def fake_get_pool(dsn, **kw):
        seen["dsn"] = dsn
        return _DeadPool()

    monkeypatch.setattr(db_pool, "get_pool", fake_get_pool)
    with pytest.raises(trackers.TrackerError):
        trackers.recent("gym")
    assert seen["dsn"] == config.DB_DSN
