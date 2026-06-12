"""Ace knowledge migration — bring ace.db semantic_memory into Utah's Postgres memory
through the admission gate (dedup + no-fab), with a junk filter so transient scrape/news
telemetry never pollutes Utah (Michael's no-injection rule + 'everything, gated' choice).
Lives in migrations/ (not utah/) so the runtime stays SQLite-free. Failures documented.

Also pinned here: bounded sqlite connects (timeout, read-only — the old connect could
CREATE an empty ace.db), compensating batch rollback on store-down (with an injected
rollback seam, never the live pool), and idempotent re-run via the dedup gate."""
from __future__ import annotations

import sqlite3

import pytest

from migrations import ace_knowledge as ak
from utah.embed import EmbedError
from utah.memory import AdmissionDenied, MemoryUnavailable, WriteAction, WriteResult


def test_junk_filter_drops_scrape_and_transient_keeps_facts():
    assert ak.is_junk("[TRUMP_TRUTH] The Obama Library in 10 years!", "ace") is True
    assert ak.is_junk("[No Title] - Post from June 3, 2026", "ace") is True
    assert ak.is_junk("anything", "notifier") is True          # transient agent
    assert ak.is_junk("ok", "ace") is True                     # too short to be a fact
    assert ak.is_junk("Michael maintains a project he calls 'Utah'.", "ace") is False


def test_migrate_classifies_inserted_deduped_rejected_junk():
    calls = {"n": 0}

    def fake_store(content):
        calls["n"] += 1
        if "dup" in content:
            return WriteResult(id=1, action=WriteAction.REINFORCED)
        if "bad" in content:
            raise AdmissionDenied("disallowed")
        return WriteResult(id=calls["n"], action=WriteAction.INSERTED)

    rows = [
        ("ace", "Michael maintains a project called Utah on Postgres."),  # inserted
        ("ace", "a duplicate fact about the utah project here"),         # deduped
        ("ace", "a bad fact that the gate rejects entirely now"),        # rejected
        ("notifier", "transient ping notification"),                     # junk (agent)
        ("ace", "[TRUMP_TRUTH] news scrape noise"),                      # junk (marker)
    ]
    stats = ak.migrate(rows, store_fn=fake_store)
    assert stats["read"] == 5
    assert stats["junk"] == 2
    assert stats["inserted"] == 1
    assert stats["deduped"] == 1
    assert stats["rejected"] == 1
    assert calls["n"] == 3                                       # only non-junk hit the gate
    assert stats["ok"] is True and stats["aborted"] is False     # honest completion signal


# ---------------------------------------------------------------------------
# read path: bounded, read-only sqlite connects
# ---------------------------------------------------------------------------

_FACT = "Michael maintains a project he calls Utah on Postgres row %d."


def _make_ace_db(path) -> None:
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE semantic_memory (id INTEGER PRIMARY KEY, agent_id TEXT, "
            "content TEXT, superseded_by INTEGER)"
        )
        con.executemany(
            "INSERT INTO semantic_memory (agent_id, content, superseded_by) VALUES (?,?,?)",
            [
                ("ace", _FACT % 1, None),
                ("ace", _FACT % 2, None),
                ("ace", "an old superseded fact that must not migrate", 1),
                ("ace", None, None),                    # NULL content filtered in SQL
            ],
        )
        con.commit()
    finally:
        con.close()


def test_read_ace_rows_filters_superseded_and_null_orders_and_limits(tmp_path):
    db = tmp_path / "ace.db"
    _make_ace_db(db)
    rows = ak.read_ace_rows(str(db))
    assert rows == [("ace", _FACT % 1), ("ace", _FACT % 2)]
    assert ak.read_ace_rows(str(db), limit=1) == [("ace", _FACT % 1)]


def test_read_ace_rows_missing_db_raises_and_never_creates_the_file(tmp_path):
    """The old plain ``sqlite3.connect`` CREATED an empty ace.db on a missing path and
    then failed with a misleading 'no such table'. Read-only mode must fail honestly
    and leave no file behind."""
    missing = tmp_path / "nope" / "ace.db"
    missing.parent.mkdir()
    with pytest.raises(sqlite3.Error):
        ak.read_ace_rows(str(missing))
    assert not missing.exists()


def test_read_ace_rows_connect_gets_a_real_timeout():
    seen = {}

    class _FakeCon:
        def execute(self, q):
            class _Cur:
                @staticmethod
                def fetchall():
                    return []
            return _Cur()

        def close(self):
            seen["closed"] = True

    def fake_connect(path, timeout):
        seen["path"], seen["timeout"] = path, timeout
        return _FakeCon()

    assert ak.read_ace_rows("/tmp/whatever.db", connect_fn=fake_connect) == []
    assert seen["path"] == "/tmp/whatever.db"
    assert seen["timeout"] == ak.DB_TIMEOUT_S and seen["timeout"] > 0
    assert seen["closed"] is True


# ---------------------------------------------------------------------------
# write path: compensating batch rollback on store-down
# ---------------------------------------------------------------------------

ROWS = [("ace", _FACT % i) for i in range(1, 6)]                 # 5 non-junk rows


def _silence_failures(monkeypatch):
    """Keep tests from writing to the LIVE failure log; return the recorded calls."""
    calls: list[tuple] = []
    monkeypatch.setattr(ak.failures, "record", lambda *a: calls.append(a))
    return calls


def test_migrate_store_down_rolls_back_open_batch_and_aborts(monkeypatch):
    recorded = _silence_failures(monkeypatch)
    rolled: list[list[int]] = []

    def store_fn(content):
        n = int(content.rsplit(" ", 1)[1].rstrip("."))
        if n == 3:
            raise MemoryUnavailable("postgres down")
        return WriteResult(id=100 + n, action=WriteAction.INSERTED)

    def rollback_fn(ids):
        rolled.append(list(ids))
        return {"ok": True, "deleted": len(ids), "restored": 0, "error": None}

    stats = ak.migrate(ROWS, store_fn=store_fn, rollback_fn=rollback_fn)
    assert stats["aborted"] is True and stats["ok"] is False
    assert rolled == [[101, 102]]                     # exactly the open batch, in order
    assert stats["rolled_back"] == 2
    assert stats["inserted"] == 0                     # rolled-back rows are NOT inserted
    assert stats["read"] == 3                         # rows 4-5 never attempted
    assert any("postgres down" in str(c) for c in recorded)


def test_migrate_rollback_scopes_to_open_batch_only(monkeypatch):
    """A FULL batch is durable; only the open (partial) batch is compensated."""
    _silence_failures(monkeypatch)
    rolled: list[list[int]] = []

    def store_fn(content):
        n = int(content.rsplit(" ", 1)[1].rstrip("."))
        if n == 4:
            raise MemoryUnavailable("postgres down")
        return WriteResult(id=100 + n, action=WriteAction.INSERTED)

    stats = ak.migrate(
        ROWS, store_fn=store_fn, batch_size=2,
        rollback_fn=lambda ids: (rolled.append(list(ids)) or
                                 {"ok": True, "deleted": len(ids), "restored": 0, "error": None}),
    )
    assert rolled == [[103]]                          # batch 1 (101,102) stays committed
    assert stats["inserted"] == 2 and stats["rolled_back"] == 1
    assert stats["aborted"] is True and stats["ok"] is False


def test_migrate_store_down_with_empty_batch_skips_rollback(monkeypatch):
    _silence_failures(monkeypatch)

    def store_fn(content):
        raise MemoryUnavailable("postgres down")

    def rollback_fn(ids):  # pragma: no cover - must not be called
        raise AssertionError("rollback_fn called with nothing to roll back")

    stats = ak.migrate(ROWS, store_fn=store_fn, rollback_fn=rollback_fn)
    assert stats["aborted"] is True and stats["ok"] is False
    assert stats["rolled_back"] == 0 and stats["rollback_ok"] is True


def test_migrate_rollback_failure_is_honest_and_never_raises(monkeypatch):
    recorded = _silence_failures(monkeypatch)

    def store_fn(content):
        n = int(content.rsplit(" ", 1)[1].rstrip("."))
        if n == 2:
            raise MemoryUnavailable("postgres down")
        return WriteResult(id=100 + n, action=WriteAction.INSERTED)

    stats = ak.migrate(
        ROWS, store_fn=store_fn,
        rollback_fn=lambda ids: {"ok": False, "deleted": 0, "restored": 0,
                                 "error": "still down"},
    )
    assert stats["aborted"] is True and stats["ok"] is False
    assert stats["rollback_ok"] is False
    assert stats["rolled_back"] == 0
    assert stats["inserted"] == 1                     # honest: the row is still in the store
    assert any("rollback" in str(c) for c in recorded)


def test_migrate_rollback_fn_raising_is_contained(monkeypatch):
    """migrate's 'never raises' contract holds even if an injected rollback blows up."""
    _silence_failures(monkeypatch)

    def store_fn(content):
        n = int(content.rsplit(" ", 1)[1].rstrip("."))
        if n == 2:
            raise MemoryUnavailable("postgres down")
        return WriteResult(id=100 + n, action=WriteAction.INSERTED)

    def rollback_fn(ids):
        raise RuntimeError("rollback exploded")

    stats = ak.migrate(ROWS, store_fn=store_fn, rollback_fn=rollback_fn)
    assert stats["aborted"] is True and stats["ok"] is False and stats["rollback_ok"] is False


def test_migrate_embed_error_is_per_row_not_an_abort(monkeypatch):
    """A transient embed failure documents the row and CONTINUES (the store is up);
    only store-down aborts with rollback."""
    _silence_failures(monkeypatch)

    def store_fn(content):
        n = int(content.rsplit(" ", 1)[1].rstrip("."))
        if n == 2:
            raise EmbedError("embedder hiccup")
        return WriteResult(id=100 + n, action=WriteAction.INSERTED)

    def rollback_fn(ids):  # pragma: no cover - must not be called
        raise AssertionError("EmbedError must not trigger rollback")

    stats = ak.migrate(ROWS, store_fn=store_fn, rollback_fn=rollback_fn)
    assert stats["read"] == 5 and stats["inserted"] == 4 and stats["failed"] == 1
    assert stats["aborted"] is False and stats["ok"] is True


class _FakeTxn:
    def __init__(self, calls):
        self._calls = calls

    def __enter__(self):
        self._calls.append(("BEGIN",))
        return self

    def __exit__(self, exc_type, exc, tb):
        self._calls.append(("ROLLBACK",) if exc_type else ("COMMIT",))
        return False


class _FakeCursor:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _FakeConn:
    def __init__(self, calls, fail=False):
        self._calls, self._fail = calls, fail

    def transaction(self):
        return _FakeTxn(self._calls)

    def execute(self, sql, params=None):
        import psycopg
        if self._fail:
            raise psycopg.OperationalError("pool checkout dead")
        self._calls.append((" ".join(sql.split()), params))
        return _FakeCursor(rowcount=len(params[0]) if params else 0)


class _FakePool:
    def __init__(self, calls, fail=False):
        self._calls, self._fail = calls, fail

    def connection(self):
        import contextlib

        @contextlib.contextmanager
        def cm():
            yield _FakeConn(self._calls, fail=self._fail)

        return cm()


def test_rollback_inserted_real_sql_one_txn_on_fake_connection(monkeypatch):
    """The default compensating rollback, against an injected connection: one
    transaction, un-supersede BEFORE the deletes, entity links cleared (no FK cascade)."""
    from utah import db_pool

    calls: list[tuple] = []
    monkeypatch.setattr(db_pool, "get_pool", lambda dsn: _FakePool(calls))
    res = ak.rollback_inserted([101, 102])
    assert res["ok"] is True and res["deleted"] == 2 and res["restored"] == 2
    assert calls[0] == ("BEGIN",) and calls[-1] == ("COMMIT",)
    sqls = [c[0] for c in calls[1:-1]]
    # The destructive batch is statement-bounded INSIDE its own txn (SET LOCAL —
    # scoped to this transaction, never leaks onto the shared pool connection).
    assert sqls[0].startswith("SET LOCAL statement_timeout")
    assert "UPDATE memory SET superseded_by = NULL, archived = false" in sqls[1]
    assert "DELETE FROM mem_entity WHERE mem_id = ANY" in sqls[2]
    assert "DELETE FROM memory WHERE id = ANY" in sqls[3]
    assert all(c[1] == ([101, 102],) for c in calls[2:-1])  # ids bound, never interpolated


def test_rollback_inserted_db_error_is_honest_never_raises(monkeypatch):
    from utah import db_pool

    monkeypatch.setattr(db_pool, "get_pool", lambda dsn: _FakePool([], fail=True))
    res = ak.rollback_inserted([101])
    assert res["ok"] is False and res["deleted"] == 0
    assert "dead" in res["error"]


def test_rollback_inserted_empty_ids_is_a_noop_without_db(monkeypatch):
    from utah import db_pool

    def boom(dsn):  # pragma: no cover - must not be called
        raise AssertionError("no ids -> no pool touch")

    monkeypatch.setattr(db_pool, "get_pool", boom)
    assert ak.rollback_inserted([]) == {"ok": True, "deleted": 0, "restored": 0,
                                        "error": None}


def test_migrate_rerun_is_idempotent_via_dedup_gate(monkeypatch):
    """Production already migrated 9,179 facts (2026-06-06). A re-run must re-offer every
    row to the gate and come back all-dedup: zero new inserts, nothing to roll back."""
    _silence_failures(monkeypatch)

    def store_fn(content):                            # gate sees known content -> REINFORCED
        return WriteResult(id=7, action=WriteAction.REINFORCED)

    def rollback_fn(ids):  # pragma: no cover - must not be called
        raise AssertionError("nothing to roll back on a clean re-run")

    stats = ak.migrate(ROWS, store_fn=store_fn, rollback_fn=rollback_fn)
    assert stats["read"] == 5 and stats["deduped"] == 5
    assert stats["inserted"] == 0 and stats["rolled_back"] == 0
    assert stats["ok"] is True and stats["aborted"] is False
