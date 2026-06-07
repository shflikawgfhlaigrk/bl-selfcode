"""Tasks capability — Ace's tasks transitions here (NOT an agent): a real Postgres-backed
todo store (add/list/done). REAL Postgres integration test (Michael's standard), self-
cleaning, skips if the DB is down."""
from __future__ import annotations

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
