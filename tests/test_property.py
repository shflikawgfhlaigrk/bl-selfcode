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
    r = prop.resolve_property("JOY JOWERS", "testco", fetch=_arcgis_hit)
    assert r["available"] and r["address"] == "123 Pine St" and r["parcel"] == "H-42"
    assert r["lat"] == 32.76 and r["lng"] == -84.87


def test_resolve_property_owner_not_found_is_honest():
    r = prop.resolve_property("NOBODY", "testco", fetch=lambda u, w: {"features": []})
    assert r["available"] is True and r["address"] is None      # honest miss, not fabricated


def test_resolve_property_extracts_arv_from_county_value_field():
    # harris is registered with value_field='Value' (the county fair-market value)
    fetch = lambda u, w: {"features": [{"attributes": {
        "Owner": "JOY JOWERS", "PhisicalAddress": "1 Pine St", "PARCEL_NO": "001",
        "Value": "108892"}}]}
    r = prop.resolve_property("JOY JOWERS", "harris", fetch=fetch)
    assert r["address"] == "1 Pine St" and r["arv"] == 108892   # real value, a deal with a price


def test_to_money_parses_real_values_and_rejects_zero_and_junk():
    assert prop._to_money("108892") == 108892
    assert prop._to_money(150800) == 150800
    assert prop._to_money("$1,234") == 1234
    for bad in (0, "0", "", None, "n/a"):
        assert prop._to_money(bad) is None                      # never a fabricated $0 ARV


def test_resolve_property_no_source_gates_not_fabricates():
    failures.set_store(FakeFailureStore())
    r = prop.resolve_property("JOY JOWERS", "testco")          # no fetch, county not registered
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
    out = prop.enrich("JOY JOWERS", "testco", fetch=arcgis, geocode_fetch=geo,
                      places_fetch=places, smb_fetch=smbs)
    assert out["address"] == "9 Oak St" and out["lat"] == 32.7      # geocode fallback worked
    assert "Nearby Shop" in out["nearby"]["no_website_smbs"]


def test_enrich_unresolved_is_honest_marker():
    failures.set_store(FakeFailureStore())
    out = prop.enrich("JOY JOWERS", "testco")                  # no source → unresolved
    assert out["property"] == "unresolved" and "nearby" not in out


def test_owner_mailing_address_generic_address_block():
    """Bulloch-style tax layers carry the owner mailing block as ADDRESS1/2/3 +
    CITY/STATE/ZIP (no MAIL*/OWNER* prefix) — live-verified 2026-06-09: PARRONDO's
    mailing addr '202 HIGHLAND ROAD, STATESBORO GA 30458' differs from the situs."""
    attrs = {"ADDRESS1": " ", "ADDRESS2": "202 HIGHLAND ROAD", "ADDRESS3": " ",
             "CITY": "STATESBORO", "STATE": "GA", "ZIP": "30458",
             "FULL_ADDRE": "2255 OLD RIGGS MILL RD"}
    mail = prop._owner_mailing_address(attrs)
    assert mail["street"] == "202 HIGHLAND ROAD"
    assert mail["zip"] == "30458"
    assert "STATESBORO" in mail["full"]


def test_owner_mailing_address_prefers_explicit_mail_fields():
    attrs = {"MAILADDR": "PO BOX 9", "MAILCITY": "MACON", "ADDRESS2": "1 Other St"}
    mail = prop._owner_mailing_address(attrs)
    assert mail["street"] == "PO BOX 9"


def test_enrich_payload_carries_owner_mail_and_situs_fallback():
    """enrich() must pass owner_mail through to heir_contact (the missing key that
    left 33 resolved rows letter-blocked), and when the county layer has no mailing
    block it must offer the geocoded SITUS as an explicit to-property fallback."""
    def fake_fetch(url, where):
        return {"features": [{"attributes": {"Owner": "DOE JOHN", "PhisicalAddress": "1 Main St",
                                              "MAILADDR": "PO BOX 1", "MAILCITY": "PINE MOUNTAIN",
                                              "MAILSTATE": "GA", "MAILZIP": "31822"},
                              "geometry": {"x": -85.0, "y": 32.8}}]}
    out = prop.enrich("JOHN DOE", "harris", fetch=fake_fetch,
                              places_fetch=lambda *a, **k: {"places": []},
                              smb_fetch=lambda *a, **k: [])
    assert out["owner_mail"]["street"] == "PO BOX 1"

    def fake_fetch_no_mail(url, where):
        return {"features": [{"attributes": {"Owner": "DOE JOHN", "PhisicalAddress": "1 Main St"},
                              "geometry": {"x": -85.0, "y": 32.8}}]}
    def fake_geo(addr):
        return {"results": [{"geometry": {"location": {"lat": 32.8, "lng": -85.0}},
                              "formatted_address": "1 Main St, Hamilton, GA 31811, USA"}]}
    out2 = prop.enrich("JOHN DOE", "harris", fetch=fake_fetch_no_mail,
                               geocode_fetch=fake_geo,
                               places_fetch=lambda *a, **k: {"places": []},
                               smb_fetch=lambda *a, **k: [])
    assert out2["owner_mail"] == {}
    assert out2["situs_mail"]["to_property"] is True
    assert "1 Main St" in out2["situs_mail"]["full"]
