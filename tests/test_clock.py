"""The clock capability: time/date is the archetypal grounded answer — a model
literally cannot know the current instant, so it must be deterministic, never a
guess. The clock is injectable so it is tested at a fixed instant."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from utah.product import clock

_CST = ZoneInfo("America/Chicago")


def test_now_text_states_time_day_and_date():
    fixed = datetime(2026, 6, 6, 19, 42, tzinfo=_CST)   # Sat 7:42 PM
    text = clock.now_text(now=lambda: fixed)
    assert "Saturday" in text
    assert "June 6, 2026" in text
    assert "7:42" in text and "PM" in text


def test_midnight_and_noon_read_naturally():
    midnight = datetime(2026, 1, 1, 0, 0, tzinfo=_CST)
    noon = datetime(2026, 1, 1, 12, 0, tzinfo=_CST)
    assert "12:00 AM" in clock.now_text(now=lambda: midnight)
    assert "12:00 PM" in clock.now_text(now=lambda: noon)


def test_is_deterministic_same_instant_same_text():
    fixed = datetime(2026, 3, 14, 9, 26, tzinfo=_CST)
    assert clock.now_text(now=lambda: fixed) == clock.now_text(now=lambda: fixed)


def test_includes_a_timezone_label():
    fixed = datetime(2026, 6, 6, 19, 42, tzinfo=_CST)
    text = clock.now_text(now=lambda: fixed)
    # the abbreviation for the configured zone (CDT in June for America/Chicago)
    assert "CDT" in text or "CST" in text
