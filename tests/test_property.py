"""Property resolver + 3-mile-radius tests — all offline via injected fetch boundaries
(same convention as test_probate.py / test_leads.py). Never hits the network; proves the
resolve → geocode → radius pipeline and the HONEST gate (no fabricated address)."""
from __future__ import annotations

from utah import failures
from utah.product import property as prop
from tests.fakes import FakeFailureStore


def _arcgis_hit(url, where):
    return {"features": [{"attributes": {"OWNER": "JOY JOWERS", "SITEADDR": "123 Pine St",
            "PARCELID": "H-42"}, "geometry": {"x": -84.87, "y": 32.76}}]}


def test_resolve_property_arcgis_hit():
    r = prop.resolve_property("JOY JOWERS", "harris", fetch=_arcgis_hit)
    assert r["available"] and r["address"] == "123 Pine St" and r["parcel"] == "H-42"
    assert r["lat"] == 32.76 and r["lng"] == -84.87


def test_resolve_property_owner_not_found_is_honest():
    r = prop.resolve_property("NOBODY", "harris", fetch=lambda u, w: {"features": []})
    assert r["available"] is True and r["address"] is None      # honest miss, not fabricated


def test_resolve_property_no_source_gates_not_fabricates():
    failures.set_store(FakeFailureStore())
    r = prop.resolve_property("JOY JOWERS", "houston")          # no fetch, county not registered
    assert r["available"] is False and r["gated"] is True and r["source"] == "none"


def test_radius_check_returns_places_and_smbs():
    import json
    places = lambda lat, lng, rad: {"places": [
        {"displayName": {"text": "Comp Cafe"}, "formattedAddress": "1 Main",
         "location": {"latitude": lat, "longitude": lng}}]}
    smbs = lambda q: json.dumps({"elements": [{"tags": {"name": "Local Diner", "amenity": "restaurant"}}]})
    out = prop.radius_check(32.76, -84.87, places_fetch=places, smb_fetch=smbs)
    assert out["radius_m"] == 4828
    assert [p["name"] for p in out["places"]] == ["Comp Cafe"]
    assert "Local Diner" in out["no_website_smbs"]


def test_enrich_full_pipeline_injected():
    import json
    geo = lambda addr: {"results": [{"geometry": {"location": {"lat": 32.7, "lng": -84.9}},
                                     "formatted_address": addr}]}
    places = lambda lat, lng, rad: {"places": []}
    smbs = lambda q: json.dumps({"elements": [{"tags": {"name": "Nearby Shop", "shop": "bakery"}}]})
    # arcgis hit WITHOUT geometry → forces the geocode fallback path
    arcgis = lambda u, w: {"features": [{"attributes": {"OWNER": "X", "SITEADDR": "9 Oak St",
                                                        "PARCELID": "P1"}}]}
    out = prop.enrich("JOY JOWERS", "harris", fetch=arcgis, geocode_fetch=geo,
                      places_fetch=places, smb_fetch=smbs)
    assert out["address"] == "9 Oak St" and out["lat"] == 32.7      # geocode fallback worked
    assert "Nearby Shop" in out["comps"]["no_website_smbs"]


def test_enrich_unresolved_is_honest_marker():
    failures.set_store(FakeFailureStore())
    out = prop.enrich("JOY JOWERS", "houston")                  # no source → unresolved
    assert out["property"] == "unresolved" and "comps" not in out
