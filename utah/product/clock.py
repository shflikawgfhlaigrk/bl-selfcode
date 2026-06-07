"""Clock capability — the current time and date, grounded.

The archetypal L1 capability: a language model cannot know the current instant
(it will guess or refuse), so this is deterministic — read the real clock in
Michael's timezone and state it plainly. The clock is injectable so it is tested
at a fixed instant.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from utah import config


def _zone() -> ZoneInfo | None:
    try:
        return ZoneInfo(config.TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return None  # fall back to system-local time


def _local_now() -> datetime:
    zone = _zone()
    return datetime.now(zone) if zone else datetime.now().astimezone()


def now_text(*, now: Callable[[], datetime] = _local_now) -> str:
    """One grounded line: the current time, day, and date. e.g.
    ``"It's 7:42 PM CDT on Saturday, June 6, 2026."``"""
    dt = now()
    # %-I (no zero-pad hour) is platform-specific; derive a clean 12-hour value.
    hour12 = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    tz = dt.strftime("%Z")
    when = f"{hour12}:{dt.minute:02d} {ampm}" + (f" {tz}" if tz else "")
    date = dt.strftime("%A, %B ") + f"{dt.day}, {dt.year}"
    return f"It's {when} on {date}."


__all__ = ["now_text"]
