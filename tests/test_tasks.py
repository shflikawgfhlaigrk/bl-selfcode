"""Tasks capability — Ace's tasks transitions here (NOT an agent): a real Postgres-backed
todo store (add/list/done). REAL Postgres integration test (Michael's standard), self-
cleaning, skips if the DB is down. Plus pure tests pinning the BOUNDED connection
contract: tasks rides the shared per-DSN pool (utah.db_pool — connect_timeout +
checkout timeout), never a fresh timeout-less psycopg.connect per call."""
from __future__ import annotations

import contextlib

import psycopg
import pytest

from utah import config
from utah.product import tasks as T

MARK = "__pytest_task__"


@pytest.fixture
def store():
    try:
        T.init_schema()
    except Exception:
        pytest.skip("Postgres not reachable — tasks integration test skipped")
    yield T
    import psycopg
    with psycopg.connect(config.DB_DSN, autocommit=True) as c:
        c.execute("DELETE FROM tasks WHERE text LIKE %s", (MARK + "%",))


def test_add_list_done_roundtrip(store):
    tid = store.add(MARK + " buy milk")
    assert tid > 0
    open_texts = [t["text"] for t in store.list_tasks(status="open")]
    assert (MARK + " buy milk") in open_texts
    assert store.done(tid) is True
    open_after = [t["text"] for t in store.list_tasks(status="open")]
    assert (MARK + " buy milk") not in open_after          # moved out of open


def test_done_unknown_id_is_false(store):
    assert store.done(99999999) is False


# --- bounded connections: the shared per-DSN pool, not a timeout-less connect ----

class _FakeCursor:
    rowcount = 1

    def fetchone(self):
        return (7,)

    def fetchall(self):
        return [(1, "pooled row", "open", "2026-06-12 08:00")]


class _FakeConn:
    def __init__(self, sqls):
        self._sqls = sqls

    def execute(self, sql, params=None):
        self._sqls.append(sql)
        return _FakeCursor()


class _FakePool:
    def __init__(self, sqls):
        self._sqls = sqls

    @contextlib.contextmanager
    def connection(self):
        yield _FakeConn(self._sqls)


def test_conn_checks_out_of_shared_bounded_pool(monkeypatch):
    """tasks rides utah.db_pool's per-DSN shared pool — the pool carries
    connect_timeout=DB_CONNECT_TIMEOUT + a bounded checkout (DB_POOL_TIMEOUT), so a
    stalled Postgres can never hang the tasks panel forever."""
    from utah import db_pool
    seen, sqls = {}, []

    def fake_get_pool(dsn, **kw):
        seen["dsn"] = dsn
        return _FakePool(sqls)

    monkeypatch.setattr(db_pool, "get_pool", fake_get_pool)
    assert T.add("pooled row") == 7
    assert seen["dsn"] == config.DB_DSN                  # the shared pool, keyed on the DSN
    assert any("INSERT INTO tasks" in s for s in sqls)


def test_list_and_done_ride_the_pool(monkeypatch):
    from utah import db_pool
    sqls = []
    monkeypatch.setattr(db_pool, "get_pool", lambda dsn, **kw: _FakePool(sqls))
    rows = T.list_tasks()
    assert rows and rows[0]["text"] == "pooled row"
    assert T.done(1) is True                             # rowcount read inside the checkout
    assert any("UPDATE tasks" in s for s in sqls)


def test_pool_checkout_failure_is_taskserror(monkeypatch):
    """A dead/stalled cluster surfaces as TasksError (psycopg_pool.PoolTimeout is a
    psycopg.Error) — bounded by the pool's timeouts, never an infinite hang."""
    from utah import db_pool

    class _DeadPool:
        @contextlib.contextmanager
        def connection(self):
            raise psycopg.OperationalError("pool checkout timed out")
            yield  # pragma: no cover

    monkeypatch.setattr(db_pool, "get_pool", lambda dsn, **kw: _DeadPool())
    with pytest.raises(T.TasksError):
        T.list_tasks()
