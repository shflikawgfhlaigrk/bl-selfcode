"""Leads capability — Ace's lead_scout transitions here (NOT as an agent): query OSM
for local SMBs with no website, drop national chains, write each to the product ledger.
The Overpass fetch is injectable so these run offline; the chain filter + parse are pure."""
from __future__ import annotations

import json

from utah.product import leads


def test_chain_filter_exact_single_word_and_prefix_multiword():
    assert leads.is_national_chain("Subway") is True            # exact single word
    assert leads.is_national_chain("Waffle House #1423") is True  # multiword prefix
    assert leads.is_national_chain("Shell Crafts Boutique") is False  # local, not "shell"
    assert leads.is_national_chain("Joe's Diner") is False
    assert leads.is_national_chain("") is False


def test_build_query_targets_no_website_in_bbox():
    q = leads.build_query((33.20, -84.95, 33.55, -84.55))
    assert '["website"!~"."]' in q          # the no-website predicate
    assert '["contact:website"!~"."]' in q and '["url"!~"."]' in q  # full no-web-presence
    assert "33.2" in q and "-84.95" in q     # the bbox
    assert "out tags center" in q


_SAMPLE = json.dumps({"elements": [
    {"type": "node", "tags": {"name": "Joe's Diner", "amenity": "restaurant", "phone": "555-1"}},
    {"type": "node", "tags": {"name": "Newnan Hardware", "shop": "hardware"}},
    {"type": "node", "tags": {"name": "Subway", "amenity": "fast_food"}},     # chain -> drop
    {"type": "node", "tags": {"amenity": "cafe"}},                            # no name -> skip
]})


def test_find_parses_filters_and_shapes():
    found = leads.find_no_website_smbs((0, 0, 1, 1), fetch=lambda q: _SAMPLE)
    names = {s["name"] for s in found}
    assert names == {"Joe's Diner", "Newnan Hardware"}           # chain + no-name removed
    joe = next(s for s in found if s["name"] == "Joe's Diner")
    assert joe["kind"] == "restaurant" and joe["contact"]["phone"] == "555-1"


def test_extract_contact_pulls_phone_email_address():
    tags = {"contact:phone": "770-555-9", "email": "hi@shop.com",
            "addr:housenumber": "12", "addr:street": "Main St", "addr:city": "Newnan",
            "addr:state": "GA", "addr:postcode": "30263"}
    c = leads._extract_contact(tags)
    assert c["phone"] == "770-555-9" and c["email"] == "hi@shop.com"
    assert c["address"] == "12 Main St, Newnan, GA, 30263"
    assert leads._extract_contact({}) == {}      # nothing tagged → empty (no fabrication)


class _RecLedger:
    """Recording double for the ledger boundary (the real Ledger is tested in test_ledger)."""
    def __init__(self):
        self.calls, self._seen = [], set()

    def record_lead(self, name, kind, region, source, contact=None):
        key = (name, region)
        if key in self._seen:
            return False
        self._seen.add(key)
        self.calls.append((name, kind, region, source, contact))
        return True


def test_scout_records_new_leads_and_dedups():
    lg = _RecLedger()
    r1 = leads.scout(lg, (0, 0, 1, 1), "Coweta GA", fetch=lambda q: _SAMPLE)
    assert r1 == {"found": 2, "new": 2, "region": "Coweta GA"}
    assert {c[0] for c in lg.calls} == {"Joe's Diner", "Newnan Hardware"}
    assert lg.calls[0][3] == "osm"                               # source tag
    r2 = leads.scout(lg, (0, 0, 1, 1), "Coweta GA", fetch=lambda q: _SAMPLE)
    assert r2["new"] == 0                                        # never-twice


def test_frontier_tiles_splits_bbox_into_grid():
    # 1.0 x 1.0 degree box, 0.5 step -> 2x2 = 4 tiles
    tiles = leads.frontier_tiles((33.0, -85.0, 34.0, -84.0), step=0.5)
    assert len(tiles) == 4
    # every tile is within the parent box and has positive area
    for (s, w, n, e) in tiles:
        assert 33.0 <= s < n <= 34.0
        assert -85.0 <= w < e <= -84.0
    # tiles cover the corners
    assert (33.0, -85.0, 33.5, -84.5) in tiles
    assert (33.5, -84.5, 34.0, -84.0) in tiles


def test_frontier_tiles_handles_nondivisible_remainder():
    # 0.7 wide, 0.5 step -> 2 columns (0.5 + 0.2 remainder), clamped to parent edge
    tiles = leads.frontier_tiles((33.0, -85.0, 33.5, -84.3), step=0.5)
    east_edges = sorted({t[3] for t in tiles})
    assert east_edges[-1] == -84.3  # last column clamps to parent east edge


def test_scout_frontier_iterates_tiles_and_dedupes():
    # two adjacent tiles; second returns a lead already seen in the first
    import json as _json
    calls = {"n": 0}
    SAMPLE_A = _json.dumps({"elements": [{"tags": {"name": "Joe Plumbing", "shop": "trade"}}]})
    SAMPLE_B = _json.dumps({"elements": [{"tags": {"name": "Joe Plumbing", "shop": "trade"}},
                                         {"tags": {"name": "Acme Welding", "craft": "welder"}}]})

    def fake_fetch(query):
        calls["n"] += 1
        return SAMPLE_A if calls["n"] == 1 else SAMPLE_B

    lg = _RecLedger()
    r = leads.scout_frontier(
        lg, bbox=(33.0, -85.0, 33.0 + leads.TILE_STEP * 2, -85.0 + leads.TILE_STEP),
        region="Test Metro", fetch=fake_fetch, max_tiles=2,
    )
    assert r["tiles_scanned"] == 2
    assert r["found"] >= 3          # 1 + 2 across tiles
    assert r["new"] == 2            # Joe deduped on the second tile
    assert r["region"] == "Test Metro"


def _cursor_pair(start=0):
    """In-memory frontier cursor (load/save) so run_scheduled never touches disk."""
    box = {"i": start}
    return (lambda: box["i"]), (lambda i: box.__setitem__("i", i)), box


def test_run_scheduled_moving_frontier_hits_target_and_advances_cursor():
    import json as _json
    lg = _RecLedger()
    # each tile yields 2 fresh businesses; per-tile region means NO cross-tile dedup
    sample = _json.dumps({"elements": [
        {"tags": {"name": "Maple Diner", "amenity": "restaurant"}},
        {"tags": {"name": "Oak Hardware", "shop": "hardware"}}]})
    load, save, box = _cursor_pair(0)
    r = leads.run_scheduled(region="GA", target=3, bbox=(0, 0, 1.0, 1.0),  # 16 tiles @0.25
                            max_tiles=40, ledger=lg, fetch=lambda q: sample,
                            cursor_load=load, cursor_save=save)
    assert r["met"] is True and r["new"] >= 3            # reached the floor
    assert r["tiles_scanned"] == 2                       # 2 tiles * 2 new = 4 >= target 3
    assert box["i"] == 2                                 # cursor advanced + persisted
    # per-tile region bucket (so the same name in another town isn't falsely deduped)
    assert any(c[2].startswith("GA [") for c in lg.calls)


def test_run_scheduled_caps_tiles_and_skips_failed_tile():
    import json as _json
    lg = _RecLedger()
    good = _json.dumps({"elements": [{"tags": {"name": "Solo Cafe", "amenity": "cafe"}}]})

    def flaky(query):
        raise RuntimeError("overpass down")   # every tile fails → run survives, new=0

    r = leads.run_scheduled(region="GA", target=500, bbox=(0, 0, 1.0, 1.0),
                            max_tiles=3, ledger=lg, fetch=flaky,
                            cursor_load=lambda: 0, cursor_save=lambda i: None)
    assert r["new"] == 0 and r["tiles_scanned"] == 3 and r["met"] is False  # capped, survived
    # and a working fetch still records
    lg2 = _RecLedger()
    r2 = leads.run_scheduled(region="GA", target=1, bbox=(0, 0, 0.5, 0.5), max_tiles=40,
                             ledger=lg2, fetch=lambda q: good,
                             cursor_load=lambda: 0, cursor_save=lambda i: None)
    assert r2["new"] >= 1 and r2["met"] is True
