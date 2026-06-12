"""Maps hardening — garbled/non-dict creds files read as a gate (never a crash, zero
HTTP), the radius clamp also guards text_search, and a non-numeric radius falls back
to the 3-mile probate default instead of raising out of the never-raises boundary."""
from __future__ import annotations

from utah import failures
from utah.integrations import maps
from tests.fakes import FakeFailureStore


def _store():
    s = FakeFailureStore()
    failures.set_store(s)
    return s


def _no_network(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("HTTP must not fire")

    monkeypatch.setattr(maps, "_http_json", boom)


# ── creds-file honesty ────────────────────────────────────────────────────────

def test_garbled_creds_file_reads_as_no_key(tmp_path, monkeypatch):
    creds = tmp_path / "maps.json"
    creds.write_text("{definitely not json")
    monkeypatch.setattr(maps, "MAPS_CREDS", creds)
    assert maps._key() == ""


def test_non_dict_creds_file_reads_as_no_key(tmp_path, monkeypatch):
    creds = tmp_path / "maps.json"
    creds.write_text('["a", "list"]')
    monkeypatch.setattr(maps, "MAPS_CREDS", creds)
    assert maps._key() == ""


def test_garbled_creds_gate_all_endpoints_with_zero_http(tmp_path, monkeypatch):
    store = _store()
    creds = tmp_path / "maps.json"
    creds.write_text("not json at all")
    monkeypatch.setattr(maps, "MAPS_CREDS", creds)
    _no_network(monkeypatch)
    assert maps.geocode("1 Main St")["gated"] is True
    assert maps.nearby(33.7, -84.4)["gated"] is True
    assert maps.text_search("plumber")["gated"] is True
    assert len([r for r in store.rows if r[2] == "gated"]) == 3


# ── radius clamp edges ────────────────────────────────────────────────────────

def test_text_search_radius_also_clamped(monkeypatch):
    _store()
    seen = {}

    def fetch(q, la, ln, rad):
        seen["rad"] = rad
        return {"places": []}

    maps.text_search("q", lat=33.0, lng=-84.0, radius_m=999_999, fetch=fetch)
    assert seen["rad"] == 50_000
    maps.text_search("q", lat=33.0, lng=-84.0, radius_m=-5, fetch=fetch)
    assert seen["rad"] == 1


def test_clamp_radius_non_numeric_falls_back_to_three_miles():
    assert maps._clamp_radius("not a number") == maps.THREE_MILES_M
    assert maps._clamp_radius(None) == maps.THREE_MILES_M
    assert maps._clamp_radius(4828.9) == 4828            # numeric passthrough, int()ed


def test_nearby_non_numeric_radius_never_raises():
    _store()
    r = maps.nearby(33.0, -84.0, "garbage", fetch=lambda la, ln, rad: {"places": []})
    assert r["available"] is True and r["radius_m"] == maps.THREE_MILES_M
