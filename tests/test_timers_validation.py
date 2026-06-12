"""Timers input validation — a garbage timer id must be rejected BEFORE any DB
round-trip (the pool seam is poisoned to prove no checkout happens)."""
from __future__ import annotations

import pytest

from utah import db_pool
from utah.product import timers


def _poisoned_pool(dsn):
    raise AssertionError("DB touched during pure validation")


def test_cancel_rejects_non_numeric_id_before_db(monkeypatch):
    monkeypatch.setattr(db_pool, "get_pool", _poisoned_pool)
    with pytest.raises(timers.TimerError):
        timers.cancel("not-an-id")
    with pytest.raises(timers.TimerError):
        timers.cancel(None)


def test_cancel_accepts_numeric_string_id(monkeypatch):
    """'42' is a valid id (deck inputs arrive as strings) — coerced, not rejected;
    the poisoned pool then proves the call would proceed to the store."""
    monkeypatch.setattr(db_pool, "get_pool", _poisoned_pool)
    with pytest.raises(AssertionError):
        timers.cancel("42")
