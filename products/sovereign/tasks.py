"""Tasks capability — a real Postgres-backed todo store.

Ace's tasks transitions HERE as a capability behind the brain, not an agent. Add/list/done
against a ``tasks`` table in the same Postgres cluster (no SQLite). Real, ungated, useful.
"""
from __future__ import annotations

import contextlib
import logging

import psycopg

from utah import UtahError, config

log = logging.getLogger("utah.product.tasks")

_DDL = """
CREATE TABLE IF NOT EXISTS tasks (
  id bigserial PRIMARY KEY,
  text text NOT NULL,
  status text NOT NULL DEFAULT 'open',
  ts timestamptz NOT NULL DEFAULT now(),
  done_ts timestamptz
);
"""


class TasksError(UtahError):
    """The tasks store could not be reached."""


@contextlib.contextmanager
def _conn():
    """An autocommit connection from the shared per-DSN pool (:mod:`utah.db_pool`) —
    the same pool the failure store rides, so every checkout is BOUNDED
    (``connect_timeout=DB_CONNECT_TIMEOUT``, checkout capped at ``DB_POOL_TIMEOUT``).
    The previous fresh ``psycopg.connect`` per call had NO connect timeout: a stalled
    Postgres hung the tasks panel forever. Any psycopg failure (checkout timeout, dead
    cluster, query error) surfaces as :class:`TasksError`."""
    from utah import db_pool

    try:
        with db_pool.get_pool(config.DB_DSN).connection() as c:
            yield c
    except psycopg.Error as exc:
        raise TasksError(f"tasks store unreachable: {exc}") from exc


def init_schema() -> None:
    with _conn() as c:
        c.execute(_DDL)


def add(text: str) -> int:
    text = (text or "").strip()
    if not text:
        raise TasksError("empty task text")
    with _conn() as c:
        row = c.execute("INSERT INTO tasks (text) VALUES (%s) RETURNING id", (text,)).fetchone()
    return int(row[0])


def list_tasks(status: str = "open", limit: int = 100) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, text, status, to_char(ts,'YYYY-MM-DD HH24:MI') "
            "FROM tasks WHERE status = %s ORDER BY id DESC LIMIT %s",
            (status, limit),
        ).fetchall()
    return [{"id": int(r[0]), "text": r[1], "status": r[2], "ts": r[3]} for r in rows]


def done(task_id: int) -> bool:
    with _conn() as c:
        cur = c.execute(
            "UPDATE tasks SET status='done', done_ts=now() WHERE id=%s AND status<>'done'",
            (task_id,),
        )
        changed = (cur.rowcount or 0) > 0    # read INSIDE the checkout (pooled conns reset on return)
    return changed


__all__ = ["init_schema", "add", "list_tasks", "done", "TasksError"]
