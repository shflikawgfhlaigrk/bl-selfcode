"""Leads edge cases — phone normalization, chain/junk filters, frontier-cursor
persistence, the bounded+honest chain purge, and the ingest audit trail.

The chain-purge tests lock the house DB rules: every Postgres touch is bounded
(connect_timeout + statement_timeout) and a dead store degrades to an honest
``{"purged": 0, "error": ...}`` — never an unhandled raise out of the cron.
"""
from __future__ import annotations

import json

import psycopg
import pytest

from utah import failures
from utah.product import leads
from tests.fakes import FakeFailureStore


@pytest.fixture(autouse=True)
def _store():
    store = FakeFailureStore()
    failures.set_store(store)
    yield store
    failures.set_store(None)


# --- normalize_phone ------------------------------------------------------------------

def test_normalize_phone_takes_first_of_packed_numbers():
    assert leads.normalize_phone("+1-770-555-1234;+1-770-555-5678") == "+17705551234"
    assert leads.normalize_phone("770-555-1234, 770-555-9999") == "+17705551234"


def test_normalize_phone_strips_extensions():
    assert leads.normalize_phone("770-555-1234 ext 5") == "+17705551234"
    assert leads.normalize_phone("770-555-1234 ext. 12") == "+17705551234"
    assert leads.normalize_phone("770-555-1234 x12") == "+17705551234"


def test_normalize_phone_eleven_digit_with_country_code():
    assert leads.normalize_phone("1 (770) 555-1234") == "+17705551234"


def test_normalize_phone_rejects_invalid_us_numbers():
    assert leads.normalize_phone("011-555-1234") == ""       # NANP area code can't start 0/1
    assert leads.normalize_phone("555-12") == ""             # too short
    assert leads.normalize_phone("") == ""
    assert leads.normalize_phone("call us!") == ""


# --- chain + junk filters ---------------------------------------------------------------

def test_single_word_chain_matches_exactly_not_as_prefix():
    assert leads.is_national_chain("Shell") is True
    assert leads.is_national_chain("Shell Crafts Boutique") is False   # real SMB kept


def test_multiword_chain_matches_as_prefix():
    assert leads.is_national_chain("Waffle House #1182") is True
    assert leads.is_national_chain("Dollar General Market") is True


def test_is_pitchable_smb_drops_malls_and_civic_pois():
    assert leads._is_pitchable_smb("5 Points Shopping Center", "convenience") is False
    assert leads._is_pitchable_smb("Newnan Chamber of Commerce", "office") is False
    assert leads._is_pitchable_smb("Anything", "mall") is False
    assert leads._is_pitchable_smb("Joe's Diner", "restaurant") is True


# --- frontier tiles + cursor -------------------------------------------------------------

def test_frontier_tiles_rejects_non_positive_step():
    with pytest.raises(ValueError):
        leads.frontier_tiles((0.0, 0.0, 1.0, 1.0), step=0)
    with pytest.raises(ValueError):
        leads.frontier_tiles((0.0, 0.0, 1.0, 1.0), step=-0.5)


def test_frontier_tiles_clamps_small_bbox_to_one_tile():
    tiles = leads.frontier_tiles((33.0, -85.0, 33.1, -84.9), step=0.25)
    assert tiles == [(33.0, -85.0, 33.1, -84.9)]


def test_tile_region_buckets_by_southwest_corner():
    assert leads._tile_region("Georgia Frontier", (33.25, -85.0, 33.5, -84.75)) \
        == "Georgia Frontier [33.25,-85.00]"


def test_cursor_roundtrip_and_corrupt_state(monkeypatch, tmp_path):
    state = tmp_path / "frontier.json"
    monkeypatch.setattr(leads, "FRONTIER_STATE", state)
    leads._save_cursor(42)
    assert leads._load_cursor() == 42
    state.write_text("{not json")
    assert leads._load_cursor() == 0                          # corrupt -> restart at 0


def test_maps_cursor_roundtrip(monkeypatch, tmp_path):
    state = tmp_path / "maps.json"
    monkeypatch.setattr(leads, "MAPS_SCOUT_STATE", state)
    leads._save_maps_cursor(7)
    assert leads._load_maps_cursor() == 7
    assert json.loads(state.read_text()) == {"i": 7}


# --- purge_multi_location_chains: bounded + honest ----------------------------------------

def test_chain_purge_uses_bounded_connection(monkeypatch):
    """House rule: NO unbounded Postgres call. The purge must pass connect_timeout
    and a statement_timeout so a stalled cluster can't hang the leads cron."""
    seen = {}

    class _Cur:
        rowcount = 0

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
    out = leads.purge_multi_location_chains()
    assert out == {"purged": 0}
    assert seen.get("connect_timeout"), "purge connected without a connect_timeout"
    assert "statement_timeout" in seen.get("options", ""), \
        "purge ran without a statement_timeout"


def test_chain_purge_is_honest_when_store_is_dead(monkeypatch, _store):
    def dead_connect(dsn, **kwargs):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(psycopg, "connect", dead_connect)
    out = leads.purge_multi_location_chains()
    assert out["purged"] == 0 and "refused" in out["error"]
    assert any(row[2] == "chain_purge_failed" for row in _store.rows)


# --- run_scheduled: ingest audit + cursor save --------------------------------------------

class _AuditLedger:
    def __init__(self):
        self.synced = []
        self.recorded = []

    def record_lead(self, name, kind, region, source, contact=None):
        self.recorded.append(name)
        return True

    def record_sync(self, **kw):
        self.synced.append(kw)


def _empty_fetch(query):
    return json.dumps({"elements": []})


def test_run_scheduled_records_ingest_audit_row():
    lg = _AuditLedger()
    saved = []
    out = leads.run_scheduled(
        bbox=(33.0, -85.0, 33.5, -84.5), region="Test Region", target=5,
        max_tiles=2, ledger=lg, fetch=_empty_fetch,
        cursor_load=lambda: 0, cursor_save=saved.append,
        foundation_gate=lambda cap: None)
    assert out["met"] is False and out["tiles_scanned"] == 2
    assert saved == [2]                                       # cursor advanced + persisted
    assert len(lg.synced) == 1
    row = lg.synced[0]
    assert row["source"] == "osm_leads" and row["status"] == "partial"
    assert row["cursor"] == "2"


def test_run_scheduled_audit_failure_never_fails_the_ingest():
    class _Boom(_AuditLedger):
        def record_sync(self, **kw):
            raise RuntimeError("sync_log missing")

    out = leads.run_scheduled(
        bbox=(33.0, -85.0, 33.2, -84.8), region="R", target=1, max_tiles=1,
        ledger=_Boom(), fetch=_empty_fetch,
        cursor_load=lambda: 0, cursor_save=lambda i: None,
        foundation_gate=lambda cap: None)
    assert out["tiles_scanned"] == 1                          # ingest completed anyway


# --- maps lane -----------------------------------------------------------------------------

def test_parse_maps_place_rejects_phoneless_and_chains():
    assert leads.parse_maps_place({"name": "No Phone Plumbing", "types": ["plumber"]}) is None
    assert leads.parse_maps_place(
        {"name": "Ace Hardware", "phone": "7705550001", "types": []}) is None
    assert leads.parse_maps_place(
        {"name": "Town Shopping Center", "phone": "7705550001", "types": []}) is None


def test_scout_maps_trades_skips_known_phone_and_enriches_existing():
    class LG:
        def __init__(self):
            self.updated = []

        def has_phone_lead(self, phone, source):
            return phone == "+17705550001"                    # already in the ledger

        def record_lead(self, name, kind, region, source, contact=None):
            return False                                      # name+region dup

        def update_lead(self, name, region, contact=None):
            self.updated.append(name)
            return True

    def fetch(q, la, ln, rad):
        return {"places": [
            {"displayName": {"text": "Known Phone Co"},
             "nationalPhoneNumber": "7705550001", "types": ["plumber"]},
            {"displayName": {"text": "Fresh Fix Co"},
             "nationalPhoneNumber": "7705550002", "types": ["plumber"]},
        ]}

    lg = LG()
    r = leads.scout_maps_trades(lg, "plumber", "Atlanta GA", 33.7, -84.4, search_fn=fetch)
    assert r["new"] == 0 and r["enriched"] == 1
    assert lg.updated == ["Fresh Fix Co"]


def test_run_maps_scheduled_advances_cursor_and_audits(monkeypatch, tmp_path):
    monkeypatch.setattr(leads, "MAPS_SCOUT_STATE", tmp_path / "maps.json")

    class LG(_AuditLedger):
        def has_phone_lead(self, phone, source):
            return False

        def update_lead(self, *a, **k):
            return False

    def fetch(q, la, ln, rad):
        return {"places": [{"displayName": {"text": f"Biz {q[:8]} {la:.2f}"},
                            "nationalPhoneNumber": "7705550003", "types": ["plumber"]}]}

    lg = LG()
    out = leads.run_maps_scheduled(ledger=lg, foundation_gate=lambda cap: None,
                                   target=1, search_fn=fetch)
    assert out["met"] is True and out["new"] >= 1
    assert leads._load_maps_cursor() == out["cursor"] > 0     # persisted for next run
    assert lg.synced and lg.synced[0]["source"] == "google_maps"


def test_run_maps_scheduled_respects_foundation_gate():
    out = leads.run_maps_scheduled(ledger=object(),
                                   foundation_gate=lambda cap: {"status": "substrate_red"})
    assert out == {"status": "substrate_red"}
