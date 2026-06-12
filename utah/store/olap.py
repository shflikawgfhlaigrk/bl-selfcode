"""DuckDB OLAP tier — columnar analytics over the Postgres primary, read-only.

The hot path (memory, leads, trades, events) lives in Postgres. Analytics
(rollups, cohorts, scorecards, the dashboard's aggregate panels) run in DuckDB,
which ATTACHes the live Utah cluster via the postgres scanner and reads it
without copying into a second store of record. Read-only by construction — the
primary stays the single source of truth.

Both boundaries are BOUNDED, never forever-hangs: the postgres ATTACH DSN always
carries a ``connect_timeout`` (a stalled primary fails the ATTACH in seconds), and
every :func:`query` runs under a watchdog thread that ``con.interrupt()``s a
stalled DuckDB scan at its time budget and raises :class:`OlapError` honestly.
"""
from __future__ import annotations

import logging
import os
import re
import threading

from utah import UtahError, config

log = logging.getLogger("utah.store.olap")

#: Seconds one OLAP query may run before the watchdog interrupts it. Analytic scans
#: are allowed far longer than an OLTP call, but never forever. Same env-override
#: pattern as config's timeout constants (config owns the Postgres connect timeout;
#: the analytic-scan budget is the OLAP tier's own knob).
QUERY_TIMEOUT_S: float = float(os.environ.get("UTAH_OLAP_QUERY_TIMEOUT", "60"))


class OlapError(UtahError):
    """DuckDB could not attach the primary or a query failed."""


def _dsn_with_connect_timeout(dsn: str, seconds: int) -> str:
    """*dsn* with ``connect_timeout`` appended when absent — keyword DSNs get a
    space-separated key, URL DSNs a query param. An explicit caller-set timeout
    is respected as-is."""
    if "connect_timeout" in dsn:
        return dsn
    if dsn.startswith(("postgresql://", "postgres://")):
        sep = "&" if "?" in dsn else "?"
        return f"{dsn}{sep}connect_timeout={int(seconds)}"
    return f"{dsn} connect_timeout={int(seconds)}".strip()


def connect():
    """A fresh in-memory DuckDB with the postgres scanner loaded."""
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - duckdb is a chosen program
        raise OlapError("duckdb is not installed (the OLAP tier)") from exc
    con = duckdb.connect(":memory:")
    con.execute("INSTALL postgres; LOAD postgres;")
    return con


def attach_primary(con, dsn: str | None = None, alias: str = "pg",
                   connect_timeout: int | None = None):
    """ATTACH the Utah Postgres cluster read-only as *alias* (default ``pg``).

    The DSN always carries a ``connect_timeout`` (default
    :data:`config.DB_CONNECT_TIMEOUT`) so a stalled primary fails the ATTACH in
    seconds instead of blocking the analytics caller forever."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias):
        raise OlapError(f"ATTACH alias must be a plain identifier, got {alias!r}")
    dsn = dsn if dsn is not None else config.DB_DSN
    dsn = _dsn_with_connect_timeout(
        dsn, connect_timeout if connect_timeout is not None else config.DB_CONNECT_TIMEOUT)
    # The DSN lands inside a SQL string literal — double any quotes so a quote in a
    # password can never break out of the ATTACH statement.
    con.execute(f"ATTACH '{dsn.replace(chr(39), chr(39) * 2)}' AS {alias} (TYPE postgres, READ_ONLY)")
    return con


def query(sql: str, dsn: str | None = None, *, timeout_s: float | None = None,
          connect_fn=None) -> list[tuple]:
    """Run one OLAP query against the attached primary. Read-only, BOUNDED.

    A watchdog thread ``con.interrupt()``s the connection after *timeout_s*
    (default :data:`QUERY_TIMEOUT_S`) so a stalled scan surfaces as an honest
    :class:`OlapError` naming the budget — never a silent forever-hang.
    *connect_fn* is the injectable connection seam (tests run with zero duckdb)."""
    budget = QUERY_TIMEOUT_S if timeout_s is None else float(timeout_s)
    con = (connect_fn or connect)()
    timed_out = threading.Event()

    def _interrupt() -> None:
        timed_out.set()
        try:
            con.interrupt()
        except Exception as exc:  # noqa: BLE001 — the watchdog must never crash the worker
            log.debug("OLAP watchdog interrupt failed: %s", exc, exc_info=True)

    watchdog = threading.Timer(budget, _interrupt)
    watchdog.daemon = True
    watchdog.start()
    try:
        attach_primary(con, dsn)
        return con.execute(sql).fetchall()
    except Exception as exc:
        if timed_out.is_set():
            raise OlapError(
                f"OLAP query exceeded {budget:g}s and was interrupted: {exc}") from exc
        raise OlapError(f"OLAP query failed: {exc}") from exc
    finally:
        watchdog.cancel()
        try:
            con.close()
        except Exception as exc:  # noqa: BLE001 — close after interrupt is best-effort
            log.debug("OLAP close after query failed: %s", exc, exc_info=True)
