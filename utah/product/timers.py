"""Timers capability — Ace's timer/pomodoro/focus_mode transition here (NOT agents): set a
timer, query which are due. Real Postgres (no SQLite); the firing notification is the notify
capability (gated). pomodoro/focus are presets over set_timer.
"""
from __future__ import annotations

import logging

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


class TimerError(UtahError):
    """Timer store unreachable."""


def _conn():
    try:
        return psycopg.connect(config.DB_DSN, autocommit=True)
    except psycopg.Error as exc:
        raise TimerError(f"timer store unreachable: {exc}") from exc


def init_schema() -> None:
    with _conn() as c:
        c.execute(_DDL)


def set_timer(label: str, seconds: float) -> int:
    label = (label or "timer").strip()
    with _conn() as c:
        row = c.execute(
            "INSERT INTO timers (label, due_ts) VALUES (%s, now() + make_interval(secs => %s)) "
            "RETURNING id", (label, float(seconds))).fetchone()
    return int(row[0])


def due() -> list[dict]:
    """Timers now due (marks them fired so they fire once). Returns the ones that just fired."""
    with _conn() as c:
        rows = c.execute(
            "UPDATE timers SET fired = true WHERE NOT fired AND due_ts <= now() "
            "RETURNING id, label", ).fetchall()
    return [{"id": int(r[0]), "label": r[1]} for r in rows]


def cancel(timer_id: int) -> bool:
    with _conn() as c:
        cur = c.execute("UPDATE timers SET fired = true WHERE id=%s AND NOT fired", (timer_id,))
    return (cur.rowcount or 0) > 0


def pomodoro(label: str = "pomodoro") -> int:
    return set_timer(label, 25 * 60)


def focus(label: str = "focus", minutes: int = 50) -> int:
    return set_timer(label, minutes * 60)


__all__ = ["init_schema", "set_timer", "due", "cancel", "pomodoro", "focus", "TimerError"]
