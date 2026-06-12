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


def test_build_query_keeps_has_website_businesses():
    """Niche-broadening (Michael, 2026-06-09): the pitch is niche-agnostic, so the
    scout must capture has-website SMBs too — they are the EASY enrichment case
    (their own site yields a precise email). The old no-web predicate capped the
    emailable pool at ~1% of supply."""
    q = leads.build_query((33.20, -84.95, 33.55, -84.55))
    assert '["website"!~"."]' not in q       # no-web exclusion is GONE
    assert '["contact:website"!~"."]' not in q and '["url"!~"."]' not in q
    assert "33.2" in q and "-84.95" in q     # the bbox
    assert "out tags center" in q


_SAMPLE = json.dumps({"elements": [
    {"type": "node", "tags": {"name": "Joe's Diner", "amenity": "restaurant",
                               "phone": "770-555-1234",
                               "website": "https://joesdiner.example"}},
    {"type": "node", "tags": {"name": "Newnan Hardware", "shop": "hardware"}},
    {"type": "node", "tags": {"name": "Subway", "amenity": "fast_food"}},     # chain -> drop
    {"type": "node", "tags": {"amenity": "cafe"}},                            # no name -> skip
]})


def test_find_parses_filters_and_shapes():
    found = leads.find_no_website_smbs((0, 0, 1, 1), fetch=lambda q: _SAMPLE)
    names = {s["name"] for s in found}
    assert names == {"Joe's Diner", "Newnan Hardware"}           # chain + no-name removed
    joe = next(s for s in found if s["name"] == "Joe's Diner")
    assert joe["kind"] == "restaurant" and joe["contact"]["phone"] == "+17705551234"
    assert joe["contact"]["website"] == "https://joesdiner.example"  # captured for enrich


def test_extract_contact_pulls_phone_email_address():
    tags = {"contact:phone": "770-555-1234", "email": "hi@shop.com",
            "addr:housenumber": "12", "addr:street": "Main St", "addr:city": "Newnan",
            "addr:state": "GA", "addr:postcode": "30263"}
    c = leads._extract_contact(tags)
    assert c["phone"] == "+17705551234" and c["email"] == "hi@shop.com"
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


_NO_GATE = lambda _cap: None


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
                            cursor_load=load, cursor_save=save,
                            foundation_gate=_NO_GATE)
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
                            cursor_load=lambda: 0, cursor_save=lambda i: None,
                            foundation_gate=_NO_GATE)
    assert r["new"] == 0 and r["tiles_scanned"] == 3 and r["met"] is False  # capped, survived
    # and a working fetch still records
    lg2 = _RecLedger()
    r2 = leads.run_scheduled(region="GA", target=1, bbox=(0, 0, 0.5, 0.5), max_tiles=40,
                             ledger=lg2, fetch=lambda q: good,
                             cursor_load=lambda: 0, cursor_save=lambda i: None,
                             foundation_gate=_NO_GATE)
    assert r2["new"] >= 1 and r2["met"] is True


def test_run_scheduled_skips_when_substrate_red():
    from utah import foundation

    skip = foundation.gate_cron(
        "leads",
        status={"ok": False, "state": "red", "anomalies": ["postgres_down"]},
    )
    r = leads.run_scheduled(
        target=1,
        bbox=(0, 0, 0.5, 0.5),
        ledger=_RecLedger(),
        fetch=lambda q: "{}",
        foundation_gate=lambda _cap: skip,
    )
    assert r["status"] == "substrate_red"
    assert r["capability"] == "leads"


# ── Overpass 429 failover + backoff (real rate-limit handling, offline) ───────
import urllib.error  # noqa: E402


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="http://test", code=code, msg="rate limited", hdrs=None, fp=None)


def test_http_fetch_fails_over_to_next_mirror_on_429_immediately():
    """A 429 is a PER-MIRROR throttle — fail over to a sibling mirror instead of
    hammering the throttled one (measured live: hammering the throttled primary cost
    ~25s/tile, failover to a healthy mirror ~0.5s). The second mirror serves the tile,
    with no inter-sweep backoff because a sibling answered on the first sweep."""
    calls: list[str] = []
    naps: list[float] = []

    def opener(url, data, headers, timeout):
        calls.append(url)
        if url == leads.OVERPASS_URLS[0]:
            raise _http_error(429)
        return '{"elements": []}'

    out = leads._http_fetch("q", opener=opener, sleep=naps.append)
    assert out == '{"elements": []}'
    # quick same-mirror retries on the throttled primary, then failover to mirror #2
    assert calls.count(leads.OVERPASS_URLS[0]) == leads.OVERPASS_MAX_RETRIES + 1
    assert calls[-1] == leads.OVERPASS_URLS[1]
    assert naps == []  # a sibling mirror answered the first sweep — no backoff


def test_http_fetch_fails_over_to_next_mirror_on_non_429_error():
    """A connection error (not 429) means the mirror is down — move on immediately,
    with NO same-mirror retry (retrying a dead mirror is pure waste)."""
    calls: list[str] = []

    def opener(url, data, headers, timeout):
        calls.append(url)
        if url == leads.OVERPASS_URLS[0]:
            raise OSError("connection refused")
        return '{"ok": 1}'

    out = leads._http_fetch("q", opener=opener, sleep=lambda _s: None)
    assert out == '{"ok": 1}'
    assert calls[0] == leads.OVERPASS_URLS[0] and calls[1] == leads.OVERPASS_URLS[1]
    assert calls.count(leads.OVERPASS_URLS[0]) == 1  # dead mirror not retried


def test_http_fetch_backs_off_and_resweeps_when_all_mirrors_throttled_once():
    """If EVERY mirror 429s in a sweep, exponential-backoff and re-sweep — recovery
    is gated on a real inter-sweep backoff having happened, so a mirror only 'recovers'
    after the first sleep. Proves the backoff path runs exactly once."""
    naps: list[float] = []

    def opener(url, data, headers, timeout):
        if not naps:           # no backoff has happened yet → still in the first sweep
            raise _http_error(429)
        return '{"recovered": 1}'   # after one inter-sweep backoff, a mirror recovers

    out = leads._http_fetch("q", opener=opener, sleep=naps.append)
    assert out == '{"recovered": 1}'
    assert len(naps) == 1 and naps[0] > 0        # exactly one inter-sweep backoff


def test_http_fetch_raises_when_all_mirrors_throttled_every_sweep():
    def opener(url, data, headers, timeout):
        raise _http_error(429)

    import pytest

    naps: list[float] = []
    with pytest.raises(RuntimeError):
        leads._http_fetch("q", opener=opener, sleep=naps.append)
    # backed off between sweeps, capped at OVERPASS_MIRROR_SWEEPS-1 backoffs
    assert len(naps) == leads.OVERPASS_MIRROR_SWEEPS - 1
