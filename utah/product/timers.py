"""Timers capability — Ace's timer/pomodoro/focus_mode transition here (NOT agents): set a
timer, query which are due. Real Postgres (no SQLite); the firing notification is the notify
capability (gated). pomodoro/focus are presets over set_timer.

Every DB touch rides the shared per-DSN pool (:mod:`utah.db_pool`) so it is BOUNDED —
``connect_timeout`` on creation, checkout capped at ``DB_POOL_TIMEOUT`` — and any psycopg
failure surfaces as :class:`TimerError` (the capability's honest error), never a raw
driver exception into the daemon. Durations are validated BEFORE the DB sees them:
``make_interval(NaN)`` would otherwise plant a timer that never (or always) fires.
"""
from __future__ import annotations

import contextlib
import logging
import math

import psycopg

from utah import UtahError, config

log = logging.getLogger("utah.product.timers")

_DDL = """
CREATE TABLE IF NOT EXISTS timers (
  id bigserial PRIMARY KEY,
  label text NOT NULL,
  due_ts timestamptz NOT NULL,
  fired boolean NOT NULL DEFAULT false,
  ts timestamptz NOT NULL DEFAULT now()
);
"""

#: Longest accepted timer: 366 days. Anything beyond is a typo, not a timer.
MAX_TIMER_SECONDS = 366 * 24 * 3600.0


class TimerError(UtahError):
    """Timer store unreachable or the request was invalid."""


@contextlib.contextmanager
def _conn():
    """An autocommit connection from the shared per-DSN pool — same bounded pattern as
    tasks.py (``connect_timeout=DB_CONNECT_TIMEOUT``, checkout capped at
    ``DB_POOL_TIMEOUT``). A fresh ``psycopg.connect`` per call had NO connect timeout:
    a stalled Postgres hung every timer op forever."""
    from utah import db_pool

    try:
        with db_pool.get_pool(config.DB_DSN).connection() as c:
            yield c
    except psycopg.Error as exc:
        raise TimerError(f"timer store unreachable: {exc}") from exc


def _valid_seconds(seconds) -> float:
    """*seconds* as a finite float ≥ 0 (a past due-time just fires on the next sweep).
    Raises :class:`TimerError` on NaN/inf/non-numeric/absurd input — BEFORE any DB call."""
    try:
        secs = float(seconds)
    except (TypeError, ValueError) as exc:
        raise TimerError(f"invalid timer duration: {seconds!r}") from exc
    if not math.isfinite(secs):
        raise TimerError(f"invalid timer duration: {seconds!r}")
    if secs > MAX_TIMER_SECONDS:
        raise TimerError(f"timer duration too long: {secs:.0f}s (max {MAX_TIMER_SECONDS:.0f}s)")
    return max(0.0, secs)


def init_schema() -> None:
    with _conn() as c:
        c.execute(_DDL)


def set_timer(label: str, seconds: float) -> int:
    secs = _valid_seconds(seconds)
    label = (str(label or "").strip() or "timer")[:200]
    with _conn() as c:
        row = c.execute(
            "INSERT INTO timers (label, due_ts) VALUES (%s, now() + make_interval(secs => %s)) "
            "RETURNING id", (label, secs)).fetchone()
        timer_id = int(row[0])           # read INSIDE the checkout (pooled conns reset on return)
    return timer_id


def due() -> list[dict]:
    """Timers now due (marks them fired so they fire once). Returns the ones that just fired."""
    with _conn() as c:
        rows = c.execute(
            "UPDATE timers SET fired = true WHERE NOT fired AND due_ts <= now() "
            "RETURNING id, label", ).fetchall()
        fired = [{"id": int(r[0]), "label": r[1]} for r in rows]
    return fired


def cancel(timer_id: int) -> bool:
    try:
        tid = int(timer_id)   # deck inputs arrive as strings; garbage rejected BEFORE the DB
    except (TypeError, ValueError) as exc:
        raise TimerError(f"invalid timer id: {timer_id!r}") from exc
    with _conn() as c:
        cur = c.execute("UPDATE timers SET fired = true WHERE id=%s AND NOT fired", (tid,))
        changed = (cur.rowcount or 0) > 0    # read INSIDE the checkout
    return changed


def pomodoro(label: str = "pomodoro") -> int:
    return set_timer(label, 25 * 60)


def focus(label: str = "focus", minutes: int = 50) -> int:
    return set_timer(label, minutes * 60)


__all__ = ["init_schema", "set_timer", "due", "cancel", "pomodoro", "focus", "TimerError",
           "MAX_TIMER_SECONDS"]
