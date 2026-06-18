"""The /trading page graphs must show REAL fire data or an honest empty state — never a
painted line. These lock the pure aggregation: torn log lines are skipped, opens pair to
closes, the P&L series tracks session_pnl, and a rostered engine that never fired still
appears (so the page can't silently hide a dormant engine). See utah.product.engine_graph.
"""
from __future__ import annotations

from utah.product import engine_graph


def test_parse_events_skips_blank_and_corrupt_lines():
    lines = [
        '{"engine":"bible","kind":"open","entry":1.0,"ts":1}',
        "",
        "   ",
        "{not json}",
        "[1,2,3]",                       # a JSON array is not a fire event
        '{"kind":"open","entry":2.0}',   # no engine → dropped
        '{"engine":"research","kind":"close","session_pnl":5,"ts":2}',
    ]
    evs = engine_graph.parse_events(lines)
    assert [e["engine"] for e in evs] == ["bible", "research"]


def test_aggregate_pairs_trades_and_tracks_pnl():
    events = [
        {"engine": "research", "kind": "open", "direction": "LONG",
         "entry": 7524.25, "stop": 7516.25, "target": 7540.25, "session_pnl": 0.0, "ts": 10},
        {"engine": "research", "kind": "close", "direction": "LONG",
         "entry": 7540.25, "session_pnl": 16.0, "ts": 20},
        {"engine": "research", "kind": "open", "direction": "SHORT",
         "entry": 7530.0, "session_pnl": 16.0, "ts": 30},  # still open at end
    ]
    data = engine_graph.aggregate(events, roster=[("research", "research-signal engine")])
    eng = next(e for e in data["engines"] if e["name"] == "research")

    assert eng["fired"] is True
    assert eng["desc"] == "research-signal engine"
    assert eng["session_pnl"] == 16.0                 # latest session_pnl wins
    assert len(eng["trades"]) == 1                     # one open→close pair
    assert eng["trades"][0]["direction"] == "LONG"
    assert eng["trades"][0]["entry"] == 7524.25        # entry comes from the OPEN
    assert len(eng["open_positions"]) == 1             # the dangling SHORT
    assert eng["open_positions"][0]["direction"] == "SHORT"
    assert [p["pnl"] for p in eng["pnl_series"]] == [0.0, 16.0, 16.0]
    assert data["totals"]["fires"] == 3
    assert data["totals"]["session_pnl"] == 16.0


def test_rostered_engine_with_no_fires_still_listed_and_empty():
    data = engine_graph.aggregate(
        [{"engine": "bible", "kind": "count_up", "trades_today": 9, "session_pnl": 310.0, "ts": 5}],
        roster=[("bible", "rule-bible engine"), ("antigrav", "antigrav emoji-signal engine")],
    )
    names = {e["name"]: e for e in data["engines"]}
    assert names["antigrav"]["fired"] is False         # dormant engine NOT hidden
    assert names["antigrav"]["pnl_series"] == []        # honest empty, no painted line
    assert names["antigrav"]["session_pnl"] == 0.0
    assert names["bible"]["trades_today"] == 9          # count_up tally honored
    assert names["bible"]["session_pnl"] == 310.0
    # producing/active engines sort before dormant ones
    assert data["engines"][0]["name"] == "bible"


def test_count_up_tally_beats_realised_close_count():
    events = [
        {"engine": "bible", "kind": "close", "session_pnl": 10, "ts": 1},
        {"engine": "bible", "kind": "count_up", "trades_today": 9, "session_pnl": 10, "ts": 2},
    ]
    data = engine_graph.aggregate(events, roster=[("bible", "")])
    assert data["engines"][0]["trades_today"] == 9     # not 1 (the close count)
