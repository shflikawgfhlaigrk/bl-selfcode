"""SICA Postgres archive boundary: the production store must be BOUNDED (connect +
statement timeout), decode both jsonb-dict and json-string rows, and keep telemetry
best-effort (a dead DB never raises into the loop). psycopg.connect is monkeypatched;
no real Postgres is touched."""
from __future__ import annotations

import pytest

from utah import sica


class _Conn:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.executed: list = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self

    def fetchall(self):
        return self.rows


def test_pg_archive_bounds_connect_and_statement(monkeypatch):
    captured = {}
    conn = _Conn()

    def fake_connect(dsn, **kw):
        captured.update(kw)
        return conn

    monkeypatch.setattr("psycopg.connect", fake_connect)
    sica._PgArchive(dsn="postgresql://x").record({"utility": 0.5, "task": "t"})
    assert captured.get("connect_timeout", 0) > 0
    assert "statement_timeout" in captured.get("options", "")
    assert any("INSERT INTO selfcode_archive" in s for s, _ in conn.executed)


def test_pg_archive_record_binds_utility_and_payload(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr("psycopg.connect", lambda dsn, **kw: conn)
    sica._PgArchive(dsn="x").record({"utility": 0.75, "task": "t"})
    insert = next(p for s, p in conn.executed if s.strip().startswith("INSERT"))
    assert insert[0] == 0.75 and '"task": "t"' in insert[1]


def test_pg_archive_entries_decode_dict_and_string_rows(monkeypatch):
    conn = _Conn(rows=[('{"utility": 0.7, "task": "a"}',), ({"utility": 0.9, "task": "b"},)])
    monkeypatch.setattr("psycopg.connect", lambda dsn, **kw: conn)
    es = sica._PgArchive(dsn="x").entries()
    assert es[0]["task"] == "a" and es[1]["task"] == "b"


def test_record_cycle_is_noop_on_mem_backend():
    sica.set_archive_backend(sica._MemArchive())
    sica.record_cycle({"x": 1})              # must not raise, must not need a DB
    assert sica.recent_cycles() == []


def test_record_cycle_never_raises_on_db_error(monkeypatch):
    sica.set_archive_backend(sica._PgArchive(dsn="x"))

    def boom(*a, **k):
        raise OSError("db down")

    monkeypatch.setattr("psycopg.connect", boom)
    sica.record_cycle({"x": 1})              # best-effort telemetry: swallow, log nothing fatal


def test_recent_cycles_empty_on_db_error(monkeypatch):
    sica.set_archive_backend(sica._PgArchive(dsn="x"))

    def boom(*a, **k):
        raise OSError("db down")

    monkeypatch.setattr("psycopg.connect", boom)
    assert sica.recent_cycles() == []        # a dead telemetry log never breaks its reader


def test_archive_record_raises_to_caller_so_selfcode_can_document(monkeypatch):
    """Unlike telemetry, the scored ARCHIVE write surfacing an error is intentional —
    selfcode._safe_record catches and reports archived=False (honest, not silent)."""
    def boom(*a, **k):
        raise OSError("db down")

    monkeypatch.setattr("psycopg.connect", boom)
    with pytest.raises(Exception):
        sica._PgArchive(dsn="x").record({"utility": 0.1})
