"""Timers hardening — bounded DB access (pooled, connect-timeout) + input validation.

The store-down tests inject a failing pool at the db_pool seam (the dependency, never the
module under test) to prove the psycopg-error → TimerError mapping without a 10s checkout
wait. The validation tests are pure (must reject BEFORE any DB round-trip)."""
from __future__ import annotations

import contextlib
import math

import psycopg
import pytest

from utah import config, db_pool
from utah.product import timers

MARK = "__pytest_timer_bounded__"


@pytest.fixture
def tm():
    try:
        timers.init_schema()
    except Exception:
        pytest.skip("Postgres not reachable")
    yield timers
    with psycopg.connect(config.DB_DSN, autocommit=True, connect_timeout=5) as c:
        c.execute("DELETE FROM timers WHERE label LIKE %s", (MARK + "%",))


def test_set_timer_rejects_non_finite_duration():
    """NaN/inf seconds must fail loudly BEFORE the DB sees them — make_interval(NaN)
    would otherwise plant a timer that never fires (or always fires)."""
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(timers.TimerError):
            timers.set_timer("x", bad)


def test_set_timer_rejects_non_numeric_duration():
    with pytest.raises(timers.TimerError):
        timers.set_timer("x", "soon")  # type: ignore[arg-type]


def test_negative_duration_means_due_now(tm):
    tm.set_timer(MARK + " past", -30)
    assert (MARK + " past") in [d["label"] for d in tm.due()]


def test_blank_label_defaults(tm):
    tid = tm.set_timer("   ", 3600)
    try:
        with psycopg.connect(config.DB_DSN, autocommit=True, connect_timeout=5) as c:
            row = c.execute("SELECT label FROM timers WHERE id=%s", (tid,)).fetchone()
        assert row[0] == "timer"
    finally:
        tm.cancel(tid)


def test_cancel_unknown_id_is_false(tm):
    assert tm.cancel(2_000_000_000) is False


def test_cancel_is_idempotent(tm):
    tid = tm.set_timer(MARK + " once", 3600)
    assert tm.cancel(tid) is True
    assert tm.cancel(tid) is False           # second cancel: nothing left to cancel


class _DeadPool:
    @contextlib.contextmanager
    def connection(self):
        raise psycopg.OperationalError("connection refused")
        yield  # pragma: no cover


def test_store_down_raises_timer_error_not_psycopg(monkeypatch):
    """The boundary contract: a dead/unreachable store surfaces as TimerError (the
    capability's own honest error), never a raw psycopg exception."""
    monkeypatch.setattr(db_pool, "get_pool", lambda dsn, **kw: _DeadPool())
    with pytest.raises(timers.TimerError, match="unreachable"):
        timers.set_timer("x", 60)
    with pytest.raises(timers.TimerError):
        timers.due()
    with pytest.raises(timers.TimerError):
        timers.cancel(1)


def test_conn_rides_the_shared_bounded_pool(monkeypatch):
    """Timers must check out of the shared per-DSN pool (connect_timeout + checkout cap),
    not open a fresh unbounded psycopg.connect per call."""
    seen = {}

    class _Recorder:
        @contextlib.contextmanager
        def connection(self):
            seen["used"] = True
            raise psycopg.OperationalError("stop here")
            yield  # pragma: no cover

    def fake_get_pool(dsn, **kw):
        seen["dsn"] = dsn
        return _Recorder()

    monkeypatch.setattr(db_pool, "get_pool", fake_get_pool)
    with pytest.raises(timers.TimerError):
        timers.due()
    assert seen["dsn"] == config.DB_DSN and seen["used"] is True
