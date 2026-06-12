"""OLAP tier boundaries are BOUNDED, never forever-hangs: the postgres ATTACH DSN
always carries a connect_timeout (a stalled primary fails in seconds), and every
query runs under a watchdog that ``con.interrupt()``s a stalled DuckDB scan and
raises OlapError honestly. All on injected fakes — the dev venv has no duckdb and
the suite must never INSTALL extensions off the network."""
from __future__ import annotations

import threading
import time

import pytest

from utah import config
from utah.store import olap


class _FakeCon:
    """Captures executed SQL; optionally stalls the analytic query until interrupted
    (the stalled-ATTACH/stalled-scan shape the watchdog exists for)."""

    def __init__(self, stall: bool = False):
        self.sqls: list[str] = []
        self.closed = False
        self.stall = stall
        self.interrupted = threading.Event()
        self.rows = [(42,)]

    def execute(self, sql):
        self.sqls.append(sql)
        if self.stall and sql.lstrip().upper().startswith("SELECT"):
            if not self.interrupted.wait(5.0):
                raise AssertionError("watchdog never interrupted the stalled query")
            raise RuntimeError("INTERRUPT: query canceled")
        return self

    def fetchall(self):
        return self.rows

    def interrupt(self):
        self.interrupted.set()

    def close(self):
        self.closed = True


# --- connect_timeout on the ATTACH DSN ------------------------------------------

def test_keyword_dsn_gets_connect_timeout():
    out = olap._dsn_with_connect_timeout("host=/tmp port=5433 dbname=utah", 5)
    assert out == "host=/tmp port=5433 dbname=utah connect_timeout=5"


def test_url_dsn_gets_connect_timeout_query_param():
    assert olap._dsn_with_connect_timeout("postgresql://u@h/db", 5).endswith(
        "?connect_timeout=5")
    assert "&connect_timeout=5" in olap._dsn_with_connect_timeout(
        "postgresql://u@h/db?sslmode=disable", 5)


def test_existing_connect_timeout_is_respected():
    dsn = "host=/tmp connect_timeout=2 dbname=utah"
    assert olap._dsn_with_connect_timeout(dsn, 5) == dsn


def test_attach_primary_appends_connect_timeout():
    con = _FakeCon()
    olap.attach_primary(con, "host=/tmp port=5433 dbname=utah")
    attach = con.sqls[-1]
    assert "ATTACH" in attach and "READ_ONLY" in attach
    assert f"connect_timeout={config.DB_CONNECT_TIMEOUT}" in attach


# --- bounded query path ----------------------------------------------------------

def test_query_returns_rows_and_closes():
    con = _FakeCon()
    rows = olap.query("SELECT count(*) FROM pg.leads",
                      dsn="host=/tmp dbname=utah", connect_fn=lambda: con)
    assert rows == [(42,)]
    assert con.closed is True


def test_query_failure_is_olaperror_and_still_closes():
    class _Boom(_FakeCon):
        def execute(self, sql):
            raise RuntimeError("postgres scanner died")

    con = _Boom()
    with pytest.raises(olap.OlapError, match="failed"):
        olap.query("SELECT 1", dsn="host=/tmp", connect_fn=lambda: con)
    assert con.closed is True


def test_stalled_query_is_interrupted_and_raises_honest_timeout():
    """The watchdog fires at timeout_s, interrupts the connection, and the failure
    surfaces as OlapError naming the budget — not a silent forever-hang."""
    con = _FakeCon(stall=True)
    t0 = time.monotonic()
    with pytest.raises(olap.OlapError, match="exceeded"):
        olap.query("SELECT * FROM pg.huge", dsn="host=/tmp",
                   connect_fn=lambda: con, timeout_s=0.2)
    assert time.monotonic() - t0 < 4.0         # interrupted, not the 5s stall
    assert con.interrupted.is_set() and con.closed is True


def test_default_query_timeout_is_sane():
    assert olap.QUERY_TIMEOUT_S > 0            # bounded by default, env-overridable


# --- ATTACH statement injection-safety -------------------------------------------

def test_attach_escapes_single_quotes_in_dsn():
    """The DSN is interpolated into the ATTACH statement — a quote in a password
    must not break out of the SQL string literal."""
    con = _FakeCon()
    olap.attach_primary(con, dsn="host=/tmp password=it's connect_timeout=5")
    attach = next(s for s in con.sqls if s.startswith("ATTACH"))
    assert "it''s" in attach and "it's' " not in attach


def test_attach_rejects_non_identifier_alias():
    con = _FakeCon()
    with pytest.raises(olap.OlapError):
        olap.attach_primary(con, dsn="host=/tmp connect_timeout=5",
                            alias="pg; DROP TABLE leads")
    assert not [s for s in con.sqls if s.startswith("ATTACH")]
