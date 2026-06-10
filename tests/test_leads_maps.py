"""Google Maps trade scout — handyman/plumber leads with phone, no website."""
from __future__ import annotations

from utah.product import leads


def test_normalize_phone_us_ten_digit():
    assert leads.normalize_phone("(770) 555-1234") == "+17705551234"


def test_parse_maps_place_keeps_no_website_with_phone():
    place = {"name": "Joe's Handyman", "phone": "(770) 555-9999",
             "address": "1 Main, Newnan GA", "website": "", "types": ["general_contractor"]}
    lead = leads.parse_maps_place(place)
    assert lead and lead["contact"]["phone"] == "+17705559999"


def test_parse_maps_place_keeps_and_stores_website():
    """Niche-broadening (Michael, 2026-06-09): has-website businesses are kept —
    their own site is the precise email-enrichment source, and the pitch is
    niche-agnostic (a $700 rebuild beats most aging sites)."""
    place = {"name": "Web Co", "phone": "7705551234", "website": "https://example.com",
             "types": ["general_contractor"]}
    lead = leads.parse_maps_place(place)
    assert lead is not None
    assert lead["contact"]["website"] == "https://example.com"
    assert lead["contact"]["phone"] == "+17705551234"


def test_find_maps_no_website_trades_uses_injected_search():
    def fetch(q, la, ln, rad):
        return {"places": [{
            "displayName": {"text": "Fix It Fast"},
            "nationalPhoneNumber": "470-555-0100",
            "formattedAddress": "Atlanta GA",
            "websiteUri": "",
            "types": ["plumber"],
            "location": {"latitude": la, "longitude": ln},
        }]}

    out = leads.find_maps_no_website_trades("handyman", 33.75, -84.39, search_fn=fetch)
    assert len(out) == 1 and out[0]["name"] == "Fix It Fast"


def test_scout_maps_trades_writes_new_lead():
    recorded = []

    class LG:
        def record_lead(self, name, kind, region, source, contact=None):
            recorded.append((name, source, contact))
            return True
        def update_lead(self, *a, **k):
            return False
        def has_phone_lead(self, phone, source="google_maps"):
            return False

    def fetch(q, la, ln, rad):
        return {"places": [{
            "displayName": {"text": "Ace Repair"},
            "nationalPhoneNumber": "7705550001",
            "formattedAddress": "GA",
            "websiteUri": "",
            "types": ["general_contractor"],
            "location": {"latitude": la, "longitude": ln},
        }]}

    r = leads.scout_maps_trades(LG(), "handyman", "Atlanta GA", 33.75, -84.39, search_fn=fetch)
    assert r["new"] == 1 and recorded[0][1] == "google_maps"
