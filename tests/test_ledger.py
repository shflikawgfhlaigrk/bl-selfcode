"""Product ledger — the Postgres schema Ace's producers transition INTO (leads /
probate / outreach / fires), with UNIQUE = never-twice and a push per write. This is
a REAL Postgres integration test (no fakes — Michael's standard): it runs against the
live Utah cluster and cleans up after itself; it SKIPS if Postgres isn't reachable so
CI without a DB stays green."""
from __future__ import annotations

import pytest

from utah import config
from utah.product.ledger import Ledger, LedgerError

MARK = "__pytest__"  # every test row is tagged so teardown can purge exactly them


@pytest.fixture
def ledger():
    lg = Ledger()
    try:
        lg.init_schema()
    except LedgerError:
        pytest.skip("Postgres not reachable — ledger integration test skipped")
    yield lg
    # teardown: purge only this test's rows
    import psycopg

    with psycopg.connect(config.DB_DSN, autocommit=True) as c:
        c.execute("DELETE FROM leads WHERE region=%s", (MARK,))
        c.execute("DELETE FROM probate WHERE county=%s", (MARK,))
        c.execute("DELETE FROM outreach_ledger WHERE campaign=%s", (MARK,))
        c.execute("DELETE FROM fires WHERE engine=%s", (MARK,))


def test_record_lead_is_never_twice(ledger):
    assert ledger.record_lead("Joe's Diner", "restaurant", MARK, "maps") is True
    assert ledger.record_lead("Joe's Diner", "restaurant", MARK, "maps") is False  # dedup


def test_outreach_suppression(ledger):
    assert ledger.log_outreach("a@b.com", MARK) is True
    assert ledger.log_outreach("a@b.com", MARK) is False  # never email twice


def test_write_emits_to_dashboard_channel():
    events = []
    lg = Ledger(publish=lambda ch, ev: events.append((ch, ev)))
    try:
        lg.init_schema()
    except LedgerError:
        pytest.skip("Postgres not reachable")
    try:
        lg.record_lead("Push Test Co", "shop", MARK, "maps")
        assert any(ch == "leads" for ch, _ in events)  # the deck gets pushed
    finally:
        import psycopg
        with psycopg.connect(config.DB_DSN, autocommit=True) as c:
            c.execute("DELETE FROM leads WHERE region=%s", (MARK,))


def test_recent_returns_shaped_rows(ledger):
    ledger.record_lead("Acme Co", "shop", MARK, "maps", contact={"phone": "555"})
    rows = ledger.recent("leads", limit=20)
    mine = [r for r in rows if r["region"] == MARK]
    assert mine and {"id", "name", "kind", "region", "status", "ts"} <= set(mine[0])


def test_recent_unknown_domain_is_empty(ledger):
    assert ledger.recent("nope") == []


def test_counts_includes_all_tables(ledger):
    c = ledger.counts()
    assert {"leads", "probate", "outreach_ledger", "fires"} <= set(c)
