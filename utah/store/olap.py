"""DuckDB OLAP tier — columnar analytics over the Postgres primary, read-only.

The hot path (memory, leads, trades, events) lives in Postgres. Analytics
(rollups, cohorts, scorecards, the dashboard's aggregate panels) run in DuckDB,
which ATTACHes the live Utah cluster via the postgres scanner and reads it
without copying into a second store of record. Read-only by construction — the
primary stays the single source of truth.
"""
from __future__ import annotations

from utah import UtahError, config


class OlapError(UtahError):
    """DuckDB could not attach the primary or a query failed."""


def connect():
    """A fresh in-memory DuckDB with the postgres scanner loaded."""
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - duckdb is a chosen program
        raise OlapError("duckdb is not installed (the OLAP tier)") from exc
    con = duckdb.connect(":memory:")
    con.execute("INSTALL postgres; LOAD postgres;")
    return con


def attach_primary(con, dsn: str | None = None, alias: str = "pg"):
    """ATTACH the Utah Postgres cluster read-only as *alias* (default ``pg``)."""
    dsn = dsn if dsn is not None else config.DB_DSN
    con.execute(f"ATTACH '{dsn}' AS {alias} (TYPE postgres, READ_ONLY)")
    return con


def query(sql: str, dsn: str | None = None) -> list[tuple]:
    """Run one OLAP query against the attached primary. Read-only."""
    con = connect()
    try:
        attach_primary(con, dsn)
        return con.execute(sql).fetchall()
    except Exception as exc:
        raise OlapError(f"OLAP query failed: {exc}") from exc
    finally:
        con.close()
