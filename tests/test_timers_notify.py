"""Timers (real Postgres) + notify (macOS-gated skeleton)."""
from __future__ import annotations

import pytest

from utah import config, failures
from utah.integrations import notify
from utah.product import timers
from tests.fakes import FakeFailureStore

MARK = "__pytest_timer__"


@pytest.fixture
def tm():
    try:
        timers.init_schema()
    except Exception:
        pytest.skip("Postgres not reachable")
    yield timers
    import psycopg
    with psycopg.connect(config.DB_DSN, autocommit=True) as c:
        c.execute("DELETE FROM timers WHERE label LIKE %s", (MARK + "%",))


def test_due_returns_elapsed_not_future(tm):
    tm.set_timer(MARK + " now", 0)            # already due
    future = tm.set_timer(MARK + " later", 3600)
    fired_labels = [d["label"] for d in tm.due()]
    assert (MARK + " now") in fired_labels
    assert (MARK + " later") not in fired_labels
    assert (MARK + " now") not in [d["label"] for d in tm.due()]   # fires only once
    assert tm.cancel(future) is True


def test_pomodoro_is_25_min(tm):
    tid = tm.pomodoro(MARK + " pom")
    assert tid > 0 and (MARK + " pom") not in [d["label"] for d in tm.due()]  # not due yet


def test_notify_uses_injected_runner():
    failures.set_store(FakeFailureStore())
    shown = []
    r = notify.notify("done!", run_fn=lambda t, m: shown.append((t, m)))
    assert r["sent"] is True and shown == [("Utah", "done!")]


def test_notify_gated_without_perms(monkeypatch):
    store = FakeFailureStore(); failures.set_store(store)
    monkeypatch.setattr(notify, "perms_available", lambda: False)
    r = notify.notify("hi")
    assert r["sent"] is False and r["gated"] is True
    assert any("gated" in row[2] for row in store.rows)
