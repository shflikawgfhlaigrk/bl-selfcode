"""Real Estate — find builders/contractors in an area, incl. within 3 miles of a property.

Reuses the proven Black Label Leads any-market finder (builders ARE a market), so this is new
orchestration over tested code, not a new scraper. Covers the spec's "find builders in the
areas" + "the 3-mile thing" (a ~3-mile radius around a point, e.g. a probate property).
Offline-testable via injected fetch + geocoder.
"""
from __future__ import annotations

import json

from utah.product import builders


class _FakeLedger:
    def __init__(self):
        self.rows = []

    def record_lead(self, name, kind, region, source, contact=None):
        if (name.lower(), region) in [(r[0].lower(), r[2]) for r in self.rows]:
            return False
        self.rows.append((name, kind, region, source, contact))
        return True


def _fetch_seq(per_call):
    calls = {"n": 0}

    def f(q):
        i = calls["n"]
        calls["n"] += 1
        return json.dumps({"elements": per_call(i)})
    return f


def test_three_mile_radius_constant_is_about_three_miles():
    assert 4.5 < builders.THREE_MILES_KM < 5.1     # 3 mi ≈ 4.83 km


def test_find_builders_merges_trade_markets_and_dedupes():
    # one distinct builder per market call → merged across BUILDER_MARKETS, deduped by name
    out = builders.find_builders((33.2, -84.9, 33.5, -84.5),
                                 fetch=_fetch_seq(lambda i: [{"tags": {"name": f"Builder {i}",
                                                                       "craft": "builder"}}]))
    names = sorted(b["name"] for b in out)
    assert names == [f"Builder {i}" for i in range(len(builders.BUILDER_MARKETS))]


def test_find_builders_near_searches_around_a_point():
    out = builders.find_builders_near(
        30.27, -97.74,
        fetch=_fetch_seq(lambda i: [{"tags": {"name": f"Pro Builders {i}", "office": "construction_company"}}]))
    assert out and out[0]["name"].startswith("Pro Builders")


def test_scout_builders_geocodes_then_records_near_property():
    led = _FakeLedger()
    res = builders.scout_builders(
        led, "123 Main St, Newnan GA",
        geocoder=lambda loc: (33.38, -84.80),
        fetch=_fetch_seq(lambda i: [{"tags": {"name": f"Acme Builders {i}", "craft": "builder"}}]))
    assert res["geocoded"] is True and res["new"] >= 1
    assert "builders" in res["region"].lower()


def test_scout_builders_honest_on_geocode_miss():
    led = _FakeLedger()
    res = builders.scout_builders(led, "Nowhere", geocoder=lambda loc: None,
                                  fetch=_fetch_seq(lambda i: []))
    assert res["geocoded"] is False and res["found"] == 0 and led.rows == []
