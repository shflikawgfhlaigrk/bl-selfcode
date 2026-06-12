"""Trackers input validation — a garbage limit falls back to the default page size
instead of surfacing a raw ValueError into the deck's trackers panel."""
from __future__ import annotations

import psycopg
import pytest

from utah import config
from utah.product import trackers

MARK = "__pytest_track_valid__"


@pytest.fixture
def tk():
    try:
        trackers.init_schema()
    except Exception:
        pytest.skip("Postgres not reachable")
    yield trackers
    with psycopg.connect(config.DB_DSN, autocommit=True, connect_timeout=5) as c:
        c.execute("DELETE FROM tracker_entries WHERE category LIKE %s", (MARK + "%",))


def test_recent_garbage_limit_is_default_not_a_crash(tk):
    tk.log_entry(MARK + "gym", "bench 3x5")
    rows = tk.recent(MARK + "gym", limit="plenty")
    assert rows and rows[0]["entry"] == "bench 3x5"   # default page, not a ValueError
    assert tk.recent(MARK + "gym", limit=None) == rows
