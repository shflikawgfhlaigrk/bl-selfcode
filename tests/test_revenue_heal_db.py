"""Revenue self-heal DB boundary: every probe query is BOUNDED (connect + statement
timeout), table names are identifier-checked before interpolation, and a dead DB is
honestly 'unassessable' — never a fabricated zero. psycopg.connect is monkeypatched;
no real Postgres is touched."""
from __future__ import annotations

import pytest

from utah import revenue_heal


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, row):
        self._row = row
        self.sql: list = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        return _Cursor(self._row)


def test_hours_since_last_bounds_connect_and_statement(monkeypatch):
    captured = {}

    def fake_connect(dsn, **kw):
        captured.update(kw)
        return _Conn(row=(5.0,))

    monkeypatch.setattr("psycopg.connect", fake_connect)
    assert revenue_heal._hours_since_last("leads") == 5.0
    assert captured.get("connect_timeout", 0) > 0                  # bounded connect
    assert "statement_timeout" in captured.get("options", "")      # bounded query


def test_count_since_bounds_connect_and_statement(monkeypatch):
    captured = {}
    conn = _Conn(row=(3,))

    def fake_connect(dsn, **kw):
        captured.update(kw)
        return conn

    monkeypatch.setattr("psycopg.connect", fake_connect)
    assert revenue_heal._count_since("mail_ledger", 26.0) == 3
    assert captured.get("connect_timeout", 0) > 0
    assert "statement_timeout" in captured.get("options", "")
    # existence probe first, then count — window is BOUND as a parameter, never interpolated
    assert len(conn.sql) == 2
    assert conn.sql[0] == ("select to_regclass(%s)", ("mail_ledger",))
    assert conn.sql[1][0].startswith("select count(*)")
    assert conn.sql[1][1] == (26.0,)


def test_non_identifier_table_is_refused_without_touching_db(monkeypatch):
    """Tables come from the fixed PRODUCERS map — but defense in depth: a non-identifier
    must never reach the f-string SQL (CWE-89 shape), and the result is 'unassessable'."""
    monkeypatch.setattr(
        "psycopg.connect",
        lambda *a, **k: pytest.fail("DB must not be touched for a bad identifier"),
    )
    assert revenue_heal._hours_since_last("leads; drop table x") is None
    assert revenue_heal._count_since('mail_ledger" --', 1.0) is None


def test_db_down_is_unassessable_not_zero(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr("psycopg.connect", boom)
    assert revenue_heal._hours_since_last("leads") is None
    assert revenue_heal._count_since("mail_ledger", 26.0) is None
    g = revenue_heal.outcome_gate()
    assert g["ok"] is False and g["assessable"] is False    # honest: never claims '$0' blindly


def test_empty_table_is_unassessable_for_age(monkeypatch):
    monkeypatch.setattr("psycopg.connect", lambda *a, **k: _Conn(row=(None,)))
    assert revenue_heal._hours_since_last("leads") is None   # no rows → cannot false-trigger


def test_every_producer_table_passes_the_identifier_guard():
    """The fixed PRODUCERS map must itself satisfy the guard — a typo'd entry would
    silently make a real producer permanently unassessable."""
    for table, _module, _idle in revenue_heal.PRODUCERS.values():
        assert revenue_heal._SAFE_TABLE.match(table), table
