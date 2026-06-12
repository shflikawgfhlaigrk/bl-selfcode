"""PostgresStore unit tests — hermetic (fake pool/connection, no live Postgres).

The integration suite (test_integration_pg.py) proves the SQL against a real server;
THESE tests pin the failure-handling contract of the transaction wrapper itself:
every psycopg/pool error surfaces as MemoryUnavailable, the borrowed connection is
ALWAYS returned to the pool (success, body error, commit error, even putconn error),
the reset_schema production guard never touches the pool, and the deck drill-down
queries clamp hostile limits.
"""
from __future__ import annotations

import psycopg
import pytest

from utah import config
from utah.memory.exceptions import MemoryUnavailable
from utah.memory.store import PostgresStore


# --- fakes ------------------------------------------------------------------

class _Result:
    def __init__(self, rows=None, rowcount=0):
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeTx:
    def __init__(self, fail_commit: bool = False):
        self.fail_commit = fail_commit

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.fail_commit:
            raise psycopg.Error("commit failed")
        return False


class _FakeConn:
    """Captures every (sql, params) executed; pops scripted results in order."""

    def __init__(self, results=None, fail_commit: bool = False,
                 execute_error: Exception | None = None):
        self.executed: list[tuple[str, object]] = []
        self._results = list(results or [])
        self.fail_commit = fail_commit
        self.execute_error = execute_error

    def transaction(self):
        return _FakeTx(self.fail_commit)

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if self.execute_error is not None:
            raise self.execute_error
        return self._results.pop(0) if self._results else _Result()


class _FakePool:
    def __init__(self, conn=None, fail_checkout: Exception | None = None,
                 fail_putconn: bool = False):
        self.conn = conn if conn is not None else _FakeConn()
        self.fail_checkout = fail_checkout
        self.fail_putconn = fail_putconn
        self.returned: list[object] = []

    def getconn(self, timeout=None):
        if self.fail_checkout is not None:
            raise self.fail_checkout
        return self.conn

    def putconn(self, conn):
        if self.fail_putconn:
            raise RuntimeError("pool closed mid-return")
        self.returned.append(conn)


def _store(pool) -> PostgresStore:
    s = PostgresStore("dbname=hermetic-unit-test")
    s._pool = lambda: pool  # instance attr shadows the method — injects the fake pool
    return s


# --- transaction wrapper: every failure is MemoryUnavailable, conn always returned ---

def test_checkout_failure_surfaces_as_memory_unavailable():
    pool = _FakePool(fail_checkout=TimeoutError("pool checkout timed out"))
    with pytest.raises(MemoryUnavailable, match="cannot get a memory connection"):
        _store(pool).reinforce(1)


def test_psycopg_error_in_body_is_wrapped_and_conn_returned():
    conn = _FakeConn(execute_error=psycopg.Error("relation does not exist"))
    pool = _FakePool(conn=conn)
    with pytest.raises(MemoryUnavailable):
        _store(pool).reinforce(1)
    assert pool.returned == [conn]  # returned exactly once despite the error


def test_commit_failure_is_wrapped_and_conn_returned():
    conn = _FakeConn(fail_commit=True)
    pool = _FakePool(conn=conn)
    with pytest.raises(MemoryUnavailable, match="commit failed"):
        _store(pool).touch([1, 2])
    assert pool.returned == [conn]


def test_success_path_returns_conn_to_pool():
    conn = _FakeConn()
    pool = _FakePool(conn=conn)
    _store(pool).touch([1])
    assert pool.returned == [conn]


def test_putconn_failure_never_raises_into_the_caller():
    pool = _FakePool(fail_putconn=True)
    _store(pool).touch([1])  # must not raise even though the return-to-pool blew up


def test_non_db_exception_passes_through_unwrapped_and_conn_returned():
    # A programming error in the caller's block must NOT masquerade as a DB outage.
    conn = _FakeConn(execute_error=KeyError("bug"))
    pool = _FakePool(conn=conn)
    with pytest.raises(KeyError):
        _store(pool).reinforce(1)
    assert pool.returned == [conn]


# --- reset_schema production guard -------------------------------------------

def test_reset_schema_refuses_without_optin_and_never_touches_the_pool(monkeypatch):
    monkeypatch.delenv("UTAH_ALLOW_RESET", raising=False)
    s = PostgresStore("dbname=hermetic-unit-test")
    s._pool = lambda: pytest.fail("reset_schema touched the pool despite the guard")
    with pytest.raises(MemoryUnavailable, match="UTAH_ALLOW_RESET"):
        s.reset_schema()


def test_reset_schema_drops_and_recreates_with_optin(monkeypatch):
    monkeypatch.setenv("UTAH_ALLOW_RESET", "1")
    conn = _FakeConn()
    _store(_FakePool(conn=conn)).reset_schema()
    sql = " ".join(s for s, _ in conn.executed)
    assert "DROP TABLE IF EXISTS" in sql and "CREATE TABLE IF NOT EXISTS memory" in sql


# --- reads: shapes + parameterization ----------------------------------------

def test_touch_and_entity_names_noop_on_empty_input():
    pool = _FakePool(conn=_FakeConn())
    s = _store(pool)
    s.touch([])
    assert s.entity_names([]) == {}
    assert pool.conn.executed == []  # no SQL for empty input


def test_live_counts_returns_int_gauges():
    conn = _FakeConn(results=[_Result([(5,)]), _Result([(3,)]), _Result([(2,)])])
    counts = _store(_FakePool(conn=conn)).live_counts()
    assert counts == {"total": 5, "live": 3, "entities": 2}


def test_nearest_builds_neighbors_and_passes_vector_params():
    conn = _FakeConn(results=[_Result([(7, "fact text", 0.91, "fact")])])
    rows = _store(_FakePool(conn=conn)).nearest([0.0] * config.EMBED_DIM, limit=4)
    assert len(rows) == 1
    assert (rows[0].id, rows[0].content, rows[0].source) == (7, "fact text", "fact")
    assert rows[0].sim == pytest.approx(0.91)
    _, params = conn.executed[0]
    assert params[-1] == 4  # the limit is parameterized, never interpolated


def test_insert_runs_in_one_transaction_with_supersede_and_links():
    conn = _FakeConn(results=[_Result([(11,)]), _Result(), _Result([(3,)]), _Result()])
    mem_id = _store(_FakePool(conn=conn)).insert(
        content="Michael lives in Newnan", source="fact", tags=["t"], confidence=0.8,
        embedding=[0.0] * config.EMBED_DIM, entity_names=["Newnan"], supersede_ids=[5],
    )
    assert mem_id == 11
    sql = " ".join(s for s, _ in conn.executed)
    assert "INSERT INTO memory" in sql
    assert "superseded_by" in sql          # the supersede ran in the SAME transaction
    assert "INSERT INTO entity" in sql and "mem_entity" in sql


# --- drill-down queries clamp hostile limits ----------------------------------

def test_list_memories_clamps_hostile_limit_and_offset():
    conn = _FakeConn(results=[_Result()])
    _store(_FakePool(conn=conn)).list_memories(limit=10**9, offset=-5)
    _, params = conn.executed[0]
    limit, offset = params
    assert 0 <= limit <= 10_000   # a hostile limit can never reach Postgres unbounded
    assert offset == 0


def test_list_entities_clamps_hostile_limit():
    conn = _FakeConn(results=[_Result()])
    _store(_FakePool(conn=conn)).list_entities(limit=10**9)
    _, params = conn.executed[0]
    assert 0 <= params[0] <= 10_000


def test_list_memories_shapes_rows_for_the_deck():
    conn = _FakeConn(results=[_Result([(9, "c", "fact", 0.8, 2, 0.93, "2026-06-12 09:00")])])
    rows = _store(_FakePool(conn=conn)).list_memories(limit=10)
    assert rows == [{"id": 9, "content": "c", "source": "fact", "confidence": 0.8,
                     "reinforcement": 2, "decay": 0.93, "ts": "2026-06-12 09:00"}]
