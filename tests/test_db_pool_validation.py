"""DSN validation at the pool registry boundary: blank DSNs are rejected loudly
on EVERY entry point (a pool that can never connect must not be cached dark
behind the '' key and shared by every misconfigured store)."""
from __future__ import annotations

import pytest

from utah import db_pool


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    monkeypatch.setattr(db_pool, "_pools", {})


def test_whitespace_only_dsn_is_rejected():
    with pytest.raises(ValueError, match="empty DSN"):
        db_pool.get_pool("   \n\t ")


def test_vector_pool_rejects_empty_dsn_too():
    with pytest.raises(ValueError, match="empty DSN"):
        db_pool.vector_pool("")


def test_rejected_dsn_caches_nothing():
    with pytest.raises(ValueError):
        db_pool.get_pool("")
    assert db_pool._pools == {}  # no dark pool left behind
