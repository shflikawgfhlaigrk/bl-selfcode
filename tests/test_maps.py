"""Maps integration boundary — honest gates (no key file, EMPTY key), injected
fetch for all three endpoints, honest miss vs honest failure, radius clamped to
Google's documented 50 km ceiling, and never-raises on a fetch blow-up."""
from __future__ import annotations

from utah import failures
from utah.integrations import maps
from tests.fakes import FakeFailureStore


def _store():
    s = FakeFailureStore()
    failures.set_store(s)
    return s


# ── gates ─────────────────────────────────────────────────────────────────────

def test_all_endpoints_gate_without_creds_file(monkeypatch):
    store = _store()
    monkeypatch.setattr(maps, "source_available", lambda: False)
    assert maps.geocode("1 Main St")["gated"] is True
    assert maps.nearby(33.7, -84.4)["gated"] is True
    assert maps.text_search("plumber Newnan GA")["gated"] is True
    assert len([r for r in store.rows if r[2] == "gated"]) == 3


def test_empty_api_key_gates_instead_of_burning_a_network_call(tmp_path, monkeypatch):
    """Creds file present but api_key empty/missing → honest gate, ZERO HTTP."""
    store = _store()
    creds = tmp_path / "maps.json"
    creds.write_text("{}")
    monkeypatch.setattr(maps, "MAPS_CREDS", creds)

    def no_network(*_a, **_k):
        raise AssertionError("HTTP must not fire with no key")

    monkeypatch.setattr(maps, "_http_json", no_network)
    assert maps.geocode("1 Main St")["gated"] is True
    assert maps.nearby(33.7, -84.4)["gated"] is True
    assert maps.text_search("plumber")["gated"] is True
    assert any(r[2] == "gated" for r in store.rows)


def test_source_available_reflects_creds_file(tmp_path, monkeypatch):
    creds = tmp_path / "maps.json"
    monkeypatch.setattr(maps, "MAPS_CREDS", creds)
    assert maps.source_available() is False
    creds.write_text('{"api_key": "k"}')
    assert maps.source_available() is True


# ── geocode ───────────────────────────────────────────────────────────────────

def test_geocode_injected_fetch_happy_path():
    _store()
    d = {"results": [{"geometry": {"location": {"lat": 33.38, "lng": -84.79}},
                      "formatted_address": "1 Main St, Newnan, GA"}]}
    r = maps.geocode("1 Main St Newnan", fetch=lambda a: d)
    assert r["available"] is True and r["gated"] is False
    assert r["lat"] == 33.38 and r["lng"] == -84.79
    assert r["formatted"] == "1 Main St, Newnan, GA"


def test_geocode_honest_miss_never_invents_a_point():
    _store()
    r = maps.geocode("xyzzy nowhere", fetch=lambda a: {"results": [], "status": "ZERO_RESULTS"})
    assert r["available"] is True and r["lat"] is None and r["lng"] is None
    assert r["status"] == "ZERO_RESULTS"


def test_geocode_fetch_failure_documented_not_raised():
    store = _store()

    def boom(a):
        raise OSError("dns down")

    r = maps.geocode("1 Main St", fetch=boom)
    assert r["available"] is False and r["gated"] is False and "dns down" in r["error"]
    assert any(row[2] == "geocode_failed" for row in store.rows)


# ── nearby ────────────────────────────────────────────────────────────────────

def test_nearby_parses_places_and_echoes_radius():
    _store()
    d = {"places": [{"displayName": {"text": "Joe's"},
                     "formattedAddress": "2 Oak St",
                     "location": {"latitude": 33.1, "longitude": -84.2}}]}
    r = maps.nearby(33.0, -84.0, 1000, fetch=lambda la, ln, rad: d)
    assert r["available"] is True and r["radius_m"] == 1000
    assert r["places"] == [{"name": "Joe's", "address": "2 Oak St",
                            "lat": 33.1, "lng": -84.2}]


def test_nearby_radius_clamped_to_google_ceiling():
    """Places API rejects radius > 50 km — clamp instead of a guaranteed 400."""
    _store()
    seen = {}

    def fetch(la, ln, rad):
        seen["rad"] = rad
        return {"places": []}

    r = maps.nearby(33.0, -84.0, 999_999, fetch=fetch)
    assert seen["rad"] == 50_000 and r["radius_m"] == 50_000
    maps.nearby(33.0, -84.0, 0, fetch=fetch)
    assert seen["rad"] == 1                      # floor: never a zero/negative radius


def test_nearby_fetch_failure_documented_not_raised():
    store = _store()

    def boom(la, ln, rad):
        raise RuntimeError("quota")

    r = maps.nearby(33.0, -84.0, fetch=boom)
    assert r["available"] is False and "quota" in r["error"]
    assert any(row[2] == "nearby_failed" for row in store.rows)


# ── text_search ───────────────────────────────────────────────────────────────

def test_text_search_parses_contact_fields():
    _store()
    d = {"places": [{"displayName": {"text": "Fix It"},
                     "formattedAddress": "Atlanta GA",
                     "nationalPhoneNumber": " 470-555-0100 ",
                     "websiteUri": " https://fixit.example ",
                     "types": ["plumber"],
                     "location": {"latitude": 33.7, "longitude": -84.4}}]}
    r = maps.text_search("plumber atlanta", fetch=lambda q, la, ln, rad: d)
    assert r["available"] is True and r["query"] == "plumber atlanta"
    p = r["places"][0]
    assert p["phone"] == "470-555-0100" and p["website"] == "https://fixit.example"
    assert p["types"] == ["plumber"] and p["lat"] == 33.7


def test_text_search_missing_optionals_default_empty():
    _store()
    r = maps.text_search("q", fetch=lambda q, la, ln, rad: {"places": [{}]})
    p = r["places"][0]
    assert p["name"] == "" and p["phone"] == "" and p["website"] == "" and p["types"] == []
    assert p["lat"] is None and p["lng"] is None


def test_text_search_fetch_failure_documented_not_raised():
    store = _store()

    def boom(q, la, ln, rad):
        raise OSError("403")

    r = maps.text_search("q", fetch=boom)
    assert r["available"] is False and "403" in r["error"]
    assert any(row[2] == "text_search_failed" for row in store.rows)


def test_three_miles_constant_is_three_miles():
    assert maps.THREE_MILES_M == 4828            # 3 mi ≈ 4828 m (probate radius contract)
