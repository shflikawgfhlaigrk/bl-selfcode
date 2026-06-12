"""Marketer cron boundary — gate, pick, spotlight, and honest degrade.

``run_scheduled`` is a launchd entrypoint (``print(run_scheduled())``): it must
NEVER raise. A dead Postgres degrades to ``{"status": "store_unreachable", ...}``
with a recorded failure; the lead pick is a bounded read (connect_timeout +
statement_timeout) and injectable so these tests never touch the live store.
"""
from __future__ import annotations

import psycopg
import pytest

from utah import failures
from utah.product import marketer
from tests.fakes import FakeFailureStore


@pytest.fixture(autouse=True)
def _store():
    store = FakeFailureStore()
    failures.set_store(store)
    yield store
    failures.set_store(None)


class _Lg:
    def __init__(self):
        self.posts = []

    def record_post(self, channel, caption, media_ref, subject, status, post_id):
        self.posts.append((channel, subject, status))
        return True


# --- compose_caption edges ------------------------------------------------------------

def test_compose_caption_falls_back_for_empty_subject():
    cap = marketer.compose_caption({})
    assert "this local business" in cap and "business" in cap


def test_compose_caption_never_exceeds_instagram_limit():
    cap = marketer.compose_caption({"name": "N" * 5000, "kind": "cafe"})
    assert len(cap) <= marketer.MAX_CAPTION


# --- spotlight statuses -----------------------------------------------------------------

def test_spotlight_gated_mail_is_recorded_as_gated():
    lg = _Lg()
    out = marketer.spotlight({"name": "Gate Co", "kind": "shop"},
                             send_fn=lambda *a: {"sent": False, "gated": True}, ledger=lg)
    assert out["sent"] is False and out["status"] == "gated"
    assert lg.posts == [("email_spotlight", "Gate Co", "gated")]


def test_spotlight_failed_send_is_recorded_as_failed():
    lg = _Lg()
    out = marketer.spotlight({"name": "Fail Co", "kind": "shop"},
                             send_fn=lambda *a: {"sent": False, "error": "smtp 550"}, ledger=lg)
    assert out["status"] == "failed" and out["sent"] is False


def test_spotlight_ledger_write_failure_never_loses_the_send_result(_store):
    class _Boom:
        def record_post(self, *a, **k):
            raise RuntimeError("marketer_posts missing")

    out = marketer.spotlight({"name": "Ledger Down Co", "kind": "shop"},
                             send_fn=lambda *a: {"sent": True, "to": "x@y"}, ledger=_Boom())
    assert out["sent"] is True and out["status"] == "posted"
    assert any(row[2] == "ledger_write_failed" for row in _store.rows)


# --- run_scheduled ------------------------------------------------------------------------

def test_run_scheduled_respects_foundation_gate():
    out = marketer.run_scheduled(ledger=_Lg(),
                                 foundation_gate=lambda cap: {"status": "substrate_red"})
    assert out == {"status": "substrate_red"}


def test_run_scheduled_no_fresh_lead():
    out = marketer.run_scheduled(ledger=_Lg(), foundation_gate=lambda cap: None,
                                 pick_fn=lambda: None)
    assert out == {"status": "no_fresh_lead"}


def test_run_scheduled_spotlights_the_picked_lead_with_injected_send():
    lg = _Lg()
    sent = {}

    def fake_send(to, subject, body):
        sent.update(to=to, subject=subject)
        return {"sent": True, "to": to}

    out = marketer.run_scheduled(ledger=lg, foundation_gate=lambda cap: None,
                                 pick_fn=lambda: ("Tabby House", "cafe"),
                                 send_fn=fake_send)
    assert out["sent"] is True and out["subject"] == "Tabby House"
    assert sent["subject"] == "Spotlight: Tabby House"
    assert lg.posts == [("email_spotlight", "Tabby House", "posted")]


def test_run_scheduled_is_honest_when_store_is_dead(_store):
    def dead_pick():
        raise psycopg.OperationalError("connection refused")

    out = marketer.run_scheduled(ledger=_Lg(), foundation_gate=lambda cap: None,
                                 pick_fn=dead_pick)
    assert out["status"] == "store_unreachable" and "refused" in out["error"]
    assert any(row[2] == "store_unreachable" for row in _store.rows)


def test_default_lead_pick_uses_bounded_connection(monkeypatch):
    """House rule: the spotlight pick must pass connect_timeout + statement_timeout —
    a stalled Postgres can't hang the daily marketer cron."""
    seen = {}

    class _Cur:
        def fetchone(self):
            return None

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            return _Cur()

    def fake_connect(dsn, **kwargs):
        seen.update(kwargs)
        return _Conn()

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    assert marketer._pick_fresh_lead() is None
    assert seen.get("connect_timeout"), "pick connected without a connect_timeout"
    assert "statement_timeout" in seen.get("options", "")
