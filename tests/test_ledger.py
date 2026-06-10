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
        c.execute("DELETE FROM mail_ledger WHERE recipient=%s", (MARK,))
        c.execute("DELETE FROM marketer_posts WHERE channel=%s", (MARK,))
        c.execute("DELETE FROM sync_log WHERE source=%s", (MARK,))
        c.execute("DELETE FROM bars WHERE symbol=%s", (MARK,))


def test_record_lead_is_never_twice(ledger):
    assert ledger.record_lead("Joe's Diner", "restaurant", MARK, "maps") is True
    assert ledger.record_lead("Joe's Diner", "restaurant", MARK, "maps") is False  # dedup


def test_outreach_suppression(ledger):
    assert ledger.log_outreach("a@b.com", MARK) is True
    assert ledger.log_outreach("a@b.com", MARK) is False  # never email twice


def test_record_mail_is_never_twice(ledger):
    assert ledger.record_mail(MARK, "Following up on your roof") is True
    assert ledger.record_mail(MARK, "Following up on your roof") is False  # never-twice
    rows = [r for r in ledger.recent("mail", limit=50) if r["recipient"] == MARK]
    assert rows and rows[0]["status"] == "sent" and rows[0]["channel"] == "email"


def test_record_post_is_never_twice(ledger):
    assert ledger.record_post(MARK, "Spotlight: Joe's", "reel_01.mp4", subject="Joe's Diner") is True
    assert ledger.record_post(MARK, "Spotlight: Joe's", "reel_01.mp4", subject="Joe's Diner") is False
    rows = [r for r in ledger.recent("marketer", limit=50) if r["channel"] == MARK]
    assert rows and rows[0]["subject"] == "Joe's Diner" and rows[0]["status"] == "posted"


def test_record_sync_appends_run(ledger):
    # append-only run log: each call is a new row (no never-twice), returns the id.
    i1 = ledger.record_sync(MARK, kind="ingest", rows_in=9179, cursor="id:9179", status="ok")
    i2 = ledger.record_sync(MARK, kind="refresh", rows_in=12, status="ok")
    assert isinstance(i1, int) and isinstance(i2, int) and i2 != i1
    rows = [r for r in ledger.recent("sync", limit=50) if r["source"] == MARK]
    assert len(rows) >= 2 and rows[0]["rows_in"] in (12, 9179)


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
    assert {"leads", "probate", "outreach_ledger", "fires",
            "mail_ledger", "marketer_posts", "sync_log"} <= set(c)


def test_record_bars_is_never_twice_per_symbol_ts(ledger):
    rows = [(1_700_000_000, 1.0, 2.0, 0.5, 1.5), (1_700_000_015, 1.5, 1.6, 1.4, 1.5)]
    assert ledger.record_bars(MARK, rows, bar_seconds=15) == 2
    assert ledger.record_bars(MARK, rows, bar_seconds=15) == 0   # UNIQUE(symbol, ts)


def test_bars_before_and_between_walk_real_closes(ledger):
    from datetime import datetime, timedelta, timezone

    base = 1_700_000_000
    ledger.record_bars(MARK, [(base + 15 * i, None, None, None, float(100 + i))
                              for i in range(5)])
    cut = datetime.fromtimestamp(base + 30, tz=timezone.utc)
    assert ledger.bars_before(MARK, cut, 3) == [100.0, 101.0, 102.0]   # ts <= cut, chrono
    assert ledger.bars_between(MARK, cut, cut + timedelta(seconds=60)) == [103.0, 104.0]
    assert MARK in ledger.bar_symbols_between(cut, cut + timedelta(seconds=60))


def test_fire_grading_roundtrip_grades_exactly_once(ledger):
    fid = ledger.record_fire(MARK, "long", 101.0, symbol=MARK)
    mine = [f for f in ledger.ungraded_fires(older_than_minutes=0) if f["id"] == fid]
    assert mine and mine[0]["symbol"] == MARK and mine[0]["entry"] == 101.0
    assert ledger.grade_fire(fid, "target", 2.0) is True
    assert ledger.grade_fire(fid, "stop", -1.0) is False     # graded once, ever
    assert all(f["id"] != fid for f in ledger.ungraded_fires(older_than_minutes=0))
    row = [r for r in ledger.recent("fires", 200) if r["id"] == fid]
    assert row and row[0]["outcome"] == "target" and row[0]["pnl"] == 2.0


def test_uncontacted_smb_leads_exclude_wrong_source(ledger):
    from utah.product.ledger import SMB_OUTREACH_CAMPAIGN

    assert ledger.record_lead("SMB Co", "trade", MARK, "google_maps",
                              contact={"phone": "+15551112222"}) is True
    assert ledger.record_lead("Import Co", "trade", MARK, "manual_import",
                              contact={"phone": "+15553334444"}) is True
    phones = ledger.uncontacted_phone_leads(SMB_OUTREACH_CAMPAIGN, 10)
    names = {p["name"] for p in phones}
    assert "SMB Co" in names and "Import Co" not in names
