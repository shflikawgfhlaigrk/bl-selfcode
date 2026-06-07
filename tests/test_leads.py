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
    assert joe["kind"] == "restaurant" and joe["phone"] == "555-1"


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
