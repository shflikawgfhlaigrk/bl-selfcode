"""Per-DSN shared pool registry: caching identity, vector-key separation, close
semantics, and the exact wiring handed to psycopg_pool (bounded sizes/timeouts,
autocommit, connect_timeout). The registry logic is the system under test — the
fake stands in only for the third-party ConnectionPool. One gated test runs a
real SELECT 1 through a real pool when Utah Postgres is up."""
from __future__ import annotations

import pytest

from utah import config, db_pool


class FakePool:
    """Stands in for psycopg_pool.ConnectionPool: records ctor kwargs + close."""

    def __init__(self, dsn, **kwargs):
        self.dsn = dsn
        self.kwargs = kwargs
        self.closed = False
        self.close_raises = False

    @staticmethod
    def check_connection(conn):  # referenced as the `check=` kwarg
        return None

    def close(self):
        if self.close_raises:
            raise RuntimeError("close blew up")
        self.closed = True


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Each test gets an empty pool registry — never the process-global one."""
    monkeypatch.setattr(db_pool, "_pools", {})


@pytest.fixture
def fake_pools(monkeypatch):
    monkeypatch.setattr("psycopg_pool.ConnectionPool", FakePool)


def test_same_dsn_returns_the_same_pool(fake_pools):
    a = db_pool.get_pool("host=h dbname=a")
    b = db_pool.get_pool("host=h dbname=a")
    assert a is b


def test_different_dsns_get_different_pools(fake_pools):
    a = db_pool.get_pool("host=h dbname=a")
    b = db_pool.get_pool("host=h dbname=b")
    assert a is not b


def test_vector_pool_is_keyed_separately_from_plain(fake_pools):
    """Memory (pgvector-configured) and the failure log (plain) share a DSN but
    must NOT share a pool — different per-connection setup."""
    plain = db_pool.get_pool("host=h dbname=a")
    vec = db_pool.vector_pool("host=h dbname=a")
    assert plain is not vec
    assert vec.kwargs["configure"] is db_pool._register_vector
    assert plain.kwargs["configure"] is None


def test_pool_wiring_is_bounded_and_autocommit(fake_pools):
    pool = db_pool.get_pool("host=h dbname=a")
    assert pool.kwargs["min_size"] == 0
    assert pool.kwargs["max_size"] == config.DB_POOL_MAX
    assert pool.kwargs["timeout"] == config.DB_POOL_TIMEOUT
    assert pool.kwargs["kwargs"]["autocommit"] is True
    assert pool.kwargs["kwargs"]["connect_timeout"] == config.DB_CONNECT_TIMEOUT
    assert pool.kwargs["check"] is FakePool.check_connection


def test_max_size_override_is_honored(fake_pools):
    pool = db_pool.get_pool("host=h dbname=a", max_size=3)
    assert pool.kwargs["max_size"] == 3


def test_close_pool_closes_and_forgets(fake_pools):
    first = db_pool.get_pool("host=h dbname=a")
    db_pool.close_pool("host=h dbname=a")
    assert first.closed is True
    second = db_pool.get_pool("host=h dbname=a")
    assert second is not first  # the registry forgot the closed pool


def test_close_pool_unknown_dsn_is_a_noop(fake_pools):
    db_pool.close_pool("host=never dbname=seen")  # must not raise


def test_close_pool_vector_only_touches_the_vector_pool(fake_pools):
    plain = db_pool.get_pool("host=h dbname=a")
    vec = db_pool.vector_pool("host=h dbname=a")
    db_pool.close_pool("host=h dbname=a", vector=True)
    assert vec.closed is True and plain.closed is False
    assert db_pool.get_pool("host=h dbname=a") is plain  # plain still cached


def test_close_pool_swallows_a_raising_close(fake_pools):
    pool = db_pool.get_pool("host=h dbname=a")
    pool.close_raises = True
    db_pool.close_pool("host=h dbname=a")  # must not raise into the caller
    assert db_pool.get_pool("host=h dbname=a") is not pool  # still forgotten


def test_close_all_closes_every_pool_and_clears_the_registry(fake_pools):
    a = db_pool.get_pool("host=h dbname=a")
    v = db_pool.vector_pool("host=h dbname=a")
    b = db_pool.get_pool("host=h dbname=b")
    db_pool._close_all()
    assert a.closed and v.closed and b.closed
    assert db_pool.get_pool("host=h dbname=a") is not a  # registry was cleared


def test_close_all_swallows_a_raising_close(fake_pools):
    pool = db_pool.get_pool("host=h dbname=a")
    pool.close_raises = True
    db_pool._close_all()  # atexit hook: must never raise


def test_empty_dsn_is_rejected_loudly(fake_pools):
    with pytest.raises(ValueError):
        db_pool.get_pool("")


def test_register_vector_retries_after_creating_the_extension(monkeypatch):
    """First boot: register_vector fails (type missing) → CREATE EXTENSION → retry."""
    import psycopg

    calls = {"register": 0}
    executed: list[str] = []

    def fake_register(conn):
        calls["register"] += 1
        if calls["register"] == 1:
            raise psycopg.ProgrammingError("vector type not found")

    monkeypatch.setattr("pgvector.psycopg.register_vector", fake_register)

    class FakeConn:
        def execute(self, sql):
            executed.append(sql)

    db_pool._register_vector(FakeConn())
    assert calls["register"] == 2
    assert any("CREATE EXTENSION IF NOT EXISTS vector" in s for s in executed)


def test_real_pool_round_trip_when_postgres_is_up():
    """Honest integration: a pooled SELECT 1 against the live Utah Postgres."""
    from utah import foundation

    if not foundation.postgres_ready():
        pytest.skip("utah postgres not accepting — integration path gated")
    pool = db_pool.get_pool(config.DB_DSN)
    try:
        with pool.connection() as conn:
            assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        db_pool.close_pool(config.DB_DSN)
