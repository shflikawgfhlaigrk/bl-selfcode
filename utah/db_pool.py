"""Per-DSN shared Postgres connection pools — the in-daemon concurrency fix (B6/B10).

The memory store used ONE connection behind an RLock, so every in-daemon memory op
serialized — the 16-slot worker pool's parallelism was negated for memory I/O, and the
"concurrent OLTP at volume" rationale that picked Postgres was undercut. This module
hands out a process-global, bounded :class:`psycopg_pool.ConnectionPool` per DSN, so:

* concurrent recalls/writes run on SEPARATE connections (real concurrency, bounded);
* a dropped connection is recycled transparently (``check`` on checkout);
* every ``PostgresStore`` / failure store on the same DSN SHARES one pool (so creating
  several store objects — e.g. one per test — never leaks a thread or a connection);
* a fresh connection is configured exactly once (autocommit + pgvector registration).

Connection *creation* failures surface only when a caller asks for a connection (the
pool fills in the background), so a bad-DSN store stays cheap until used — matching the
store's "lazy, fail-honest" contract.
"""
from __future__ import annotations

import atexit
import threading
from typing import Callable

from utah import config

_pools: dict[str, object] = {}
_lock = threading.Lock()


@atexit.register
def _close_all() -> None:
    """Close every pool at normal interpreter exit, BEFORE ConnectionPool.__del__ runs —
    its finalizer joins a worker thread, which Python forbids at interpreter shutdown
    (a noisy PythonFinalizationError otherwise). The daemon's os._exit path skips atexit,
    which is fine: the OS reclaims the process wholesale."""
    with _lock:
        pools = list(_pools.values())
        _pools.clear()
    for pool in pools:
        try:
            pool.close()
        except Exception:  # noqa: BLE001
            pass


def _register_vector(conn) -> None:
    """Configure one freshly-created pooled connection: register the pgvector type
    adapter (creating the extension on first boot if needed). Runs once per connection."""
    import psycopg
    from pgvector.psycopg import register_vector

    try:
        register_vector(conn)
    except psycopg.ProgrammingError:
        # First boot: the vector type does not exist yet. Create it, then retry.
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(conn)


def _vkey(dsn: str) -> str:
    """Cache key for the pgvector-configured pool. Memory and the failure log share a
    DSN but need DIFFERENT connection setup (vector-registered vs plain), so they must
    NOT share a pool — keying the vector pool separately keeps them distinct."""
    return f"{dsn}\x00vector"


def get_pool(dsn: str, *, configure: Callable | None = None,
             max_size: int | None = None, _key: str | None = None):
    """Return the process-global pool for *dsn* (cache key ``_key`` or *dsn*), lazily.

    *configure* runs once per new connection (e.g. register pgvector); pass ``None`` for
    stores that need no per-connection setup (the failure log). The pool is opened in the
    background, so this never blocks on a dead Postgres — callers see the failure when they
    request a connection, as :class:`MemoryUnavailable`/timeout.

    Raises:
        ValueError: empty/blank *dsn* — a pool that can never connect would otherwise
            sit silently dark behind every store sharing the '' cache key."""
    if not dsn or not dsn.strip():
        raise ValueError("db_pool.get_pool: empty DSN")
    cache_key = _key or dsn
    with _lock:
        pool = _pools.get(cache_key)
        if pool is not None:
            return pool
        from psycopg_pool import ConnectionPool

        pool = ConnectionPool(
            dsn,
            min_size=0,                                   # hold nothing idle until used
            max_size=max_size or config.DB_POOL_MAX,
            open=True,
            timeout=config.DB_POOL_TIMEOUT,
            max_idle=300.0,                               # reap idle conns after 5 min
            check=ConnectionPool.check_connection,        # recycle a dropped conn on checkout
            configure=configure,
            kwargs={"autocommit": True, "connect_timeout": config.DB_CONNECT_TIMEOUT},
            name=f"utah-{abs(hash(cache_key)) % 10000}",
        )
        _pools[cache_key] = pool
        return pool


def close_pool(dsn: str, *, vector: bool = False) -> None:
    """Close and forget a pool (idempotent). ``vector=True`` closes the pgvector pool for
    *dsn*; otherwise the plain pool. The next ``get_pool``/``vector_pool`` recreates it."""
    key = _vkey(dsn) if vector else dsn
    with _lock:
        pool = _pools.pop(key, None)
    if pool is not None:
        try:
            pool.close()
        except Exception:  # noqa: BLE001 — closing must never raise into a caller
            pass


def vector_pool(dsn: str):
    """The memory store's pool: connections come pre-registered for pgvector."""
    return get_pool(dsn, configure=_register_vector, _key=_vkey(dsn))


__all__ = ["get_pool", "vector_pool", "close_pool"]
