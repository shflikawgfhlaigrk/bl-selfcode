"""Clock zone handling — the configured-timezone boundary test_clock.py leaves open:
a bad TIMEZONE must degrade to system-local (warn, never crash), a naive datetime must
not leave a dangling separator where the tz label would sit, and the default path must
read the real clock."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from utah.product import clock

#: e.g. "It's 7:42 PM CDT on Saturday, June 6, 2026."  (tz label optional)
_SENTENCE = re.compile(
    r"^It's \d{1,2}:\d{2} (AM|PM)( [A-Za-z+\-0-9]+)? on [A-Z][a-z]+day, "
    r"[A-Z][a-z]+ \d{1,2}, \d{4}\.$")


def test_invalid_timezone_warns_and_falls_back_to_system_local(monkeypatch, caplog):
    monkeypatch.setattr(clock.config, "TIMEZONE", "Not/AZone")
    with caplog.at_level(logging.WARNING, logger="utah.clock"):
        text = clock.now_text()
    assert _SENTENCE.match(text), text       # still a grounded, well-formed answer
    assert "invalid TIMEZONE" in caplog.text


def test_empty_timezone_value_is_survived(monkeypatch):
    monkeypatch.setattr(clock.config, "TIMEZONE", "")   # ZoneInfo('') raises ValueError
    assert _SENTENCE.match(clock.now_text())


def test_naive_datetime_omits_the_tz_label_without_dangling_space():
    fixed = datetime(2026, 6, 6, 19, 42)     # naive: %Z is empty
    text = clock.now_text(now=lambda: fixed)
    assert "7:42 PM on Saturday" in text     # no double space, no orphan label
    assert "  " not in text


def test_noon_boundary_eleven_am_vs_pm():
    tz = ZoneInfo("America/Chicago")
    assert "11:59 AM" in clock.now_text(now=lambda: datetime(2026, 1, 1, 11, 59, tzinfo=tz))
    assert "11:00 PM" in clock.now_text(now=lambda: datetime(2026, 1, 1, 23, 0, tzinfo=tz))


def test_default_clock_reads_the_real_instant():
    text = clock.now_text()
    assert _SENTENCE.match(text), text
    assert str(datetime.now().year) in text  # grounded in the actual current year
