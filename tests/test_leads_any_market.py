"""Leads — find people in ANY market the client requests.

The Black Label Leads spec: the client names a market (any vertical) and a location, and the
finder pulls real businesses in that market there. Known markets map to precise OSM selectors;
an unknown market falls back to the broad business query + a name/kind keyword match — so the
finder is never limited to a fixed vertical list. Geocoding turns the client's location into a
search box. All offline-testable via injected fetch + geocoder (no network).
"""
from __future__ import annotations

import json

from utah.product import leads


class _FakeLedger:
    def __init__(self):
        self.rows: list[tuple] = []

    def record_lead(self, name, kind, region, source, contact=None):
        key = (name.lower(), region)
        if key in [(r[0].lower(), r[2]) for r in self.rows]:
            return False
        self.rows.append((name, kind, region, source, contact))
        return True


def _fetch(elements):
    return lambda q: json.dumps({"elements": elements})


def _fetch_seq(per_call):
    calls = {"n": 0}

    def f(q):
        i = calls["n"]
        calls["n"] += 1
        return json.dumps({"elements": per_call(i)})
    return f


def test_known_market_maps_to_precise_osm_selector():
    assert ("amenity", "dentist") in leads.market_selectors("dentist")
    # contained-keyword match: "pediatric dentist" still resolves to dentist
    assert ("amenity", "dentist") in leads.market_selectors("pediatric dentist")
    # unknown market → no selectors → caller uses the broad query + name filter
    assert leads.market_selectors("artisanal candle maker") == []


def test_build_market_query_targets_the_requested_market():
    q = leads.build_market_query((33.2, -84.9, 33.5, -84.5), "dentist")
    assert '["amenity"="dentist"]' in q
    # unknown market falls back to the broad business query
    qb = leads.build_market_query((33.2, -84.9, 33.5, -84.5), "candle maker")
    assert '["shop"]' in qb


def test_find_market_smbs_known_market_returns_on_market_and_drops_chains():
    els = [
        {"tags": {"name": "Bright Smile Dental", "amenity": "dentist", "phone": "770-555-1234"}},
        {"tags": {"name": "Aspen Dental", "amenity": "dentist"}},  # national chain → dropped
    ]
    out = leads.find_market_smbs((33.2, -84.9, 33.5, -84.5), "dentist", fetch=_fetch(els))
    names = [o["name"] for o in out]
    assert "Bright Smile Dental" in names
    assert "Aspen Dental" not in names


def test_find_market_smbs_unknown_market_name_filters():
    els = [
        {"tags": {"name": "Aroma Candle Co", "shop": "candles"}},
        {"tags": {"name": "Joe's Pizza", "amenity": "restaurant"}},
    ]
    out = leads.find_market_smbs((33.2, -84.9, 33.5, -84.5), "candle", fetch=_fetch(els))
    assert [o["name"] for o in out] == ["Aroma Candle Co"]


def test_scout_market_records_market_tagged_leads():
    led = _FakeLedger()
    els = [{"tags": {"name": "Bright Smile Dental", "amenity": "dentist"}}]
    res = leads.scout_market(led, "dentist", bbox=(33.2, -84.9, 33.5, -84.5), fetch=_fetch(els))
    assert res["found"] == 1 and res["new"] == 1 and res["market"] == "dentist"
    assert "dentist" in res["region"]
    assert led.rows[0][0] == "Bright Smile Dental"


def test_scout_market_in_geocodes_then_searches():
    led = _FakeLedger()
    els = [{"tags": {"name": "Austin Family Dental", "amenity": "dentist"}}]
    res = leads.scout_market_in(
        led, "dentist", "Austin, TX",
        geocoder=lambda loc: (30.27, -97.74), fetch=_fetch(els))
    assert res["geocoded"] is True and res["new"] == 1
    assert "Austin, TX" in res["region"]


def test_scout_market_in_honest_on_geocode_miss():
    led = _FakeLedger()
    res = leads.scout_market_in(led, "dentist", "Nowheresville",
                                geocoder=lambda loc: None, fetch=_fetch([]))
    assert res == {"found": 0, "new": 0, "market": "dentist",
                   "region": "Nowheresville", "geocoded": False}
    assert led.rows == []


def test_bbox_around_brackets_the_point():
    s, w, n, e = leads.bbox_around(30.27, -97.74, radius_km=12)
    assert s < 30.27 < n and w < -97.74 < e


def test_us_metros_give_national_coverage():
    metros = leads.US_METROS
    assert len(metros) >= 25
    lons = [c[2] for c in metros]
    lats = [c[1] for c in metros]
    assert min(lons) < -118 and max(lons) > -75   # west coast .. east coast
    assert min(lats) < 30 and max(lats) > 44       # deep south .. northern tier


def test_scout_market_us_sweeps_every_metro():
    led = _FakeLedger()
    metros = [("Austin", 30.27, -97.74), ("Seattle", 47.60, -122.33)]

    def per_call(i):
        return [{"tags": {"name": f"Dental {i}", "amenity": "dentist"}}]
    res = leads.scout_market_us(led, "dentist", metros=metros, fetch=_fetch_seq(per_call))
    assert res["metros_scanned"] == 2
    assert res["found"] == 2 and res["new"] == 2
