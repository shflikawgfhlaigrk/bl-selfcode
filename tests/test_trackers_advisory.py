"""Trackers (Postgres log) + advisory (brain-backed personas). Trackers is a real-Postgres
integration test (self-cleaning, skips if DB down); advisory uses an injected brain."""
from __future__ import annotations

import pytest

from utah import advisory, config
from utah.product import trackers

MARK = "__pytest_track__"


@pytest.fixture
def tk():
    try:
        trackers.init_schema()
    except Exception:
        pytest.skip("Postgres not reachable")
    yield trackers
    import psycopg
    with psycopg.connect(config.DB_DSN, autocommit=True) as c:
        c.execute("DELETE FROM tracker_entries WHERE category=%s", (MARK,))


def test_tracker_log_and_recent(tk):
    tk.log_entry(MARK, "ran 3 miles")
    tk.log_entry(MARK, "bench 185")
    entries = [e["entry"] for e in tk.recent(MARK, limit=10)]
    assert "bench 185" in entries and "ran 3 miles" in entries
    assert entries[0] == "bench 185"          # newest first


def test_advise_uses_persona_and_injected_brain():
    seen = {}
    class _Reply:
        text = "Do 3 sets."
        class source:  # noqa: N801
            value = "brain"
    def fake_tell(prompt):
        seen["prompt"] = prompt
        return _Reply()
    r = advisory.advise("coach", "how do I start lifting?", tell=fake_tell)
    assert r["persona"] == "coach" and r["answer"] == "Do 3 sets."
    assert "accountability coach" in seen["prompt"]      # persona framing applied
    assert "how do I start lifting?" in seen["prompt"]


def test_advise_brain_down_is_honest():
    def boom(p):
        raise RuntimeError("brain down")
    r = advisory.advise("planner", "plan my week", tell=boom)
    assert r["source"] == "unavailable" and "unavailable" in r["answer"]


def test_lawyer_persona_is_non_binding():
    assert "not legal advice" in advisory.PERSONAS["lawyer"]
    assert advisory.PERSONAS["lawdie"] == advisory.PERSONAS["lawyer"]
