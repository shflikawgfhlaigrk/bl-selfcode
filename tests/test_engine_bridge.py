"""The engine bridge ingests the restored Apex-Prime fleet's fires into Utah.

It is the Utah-native port of AceOS's engine_listener: poll each engine's
``/api/trade_state``, diff against the prior frame, and emit a fire event on each
transition (open / close / trade-count-up). Detection is a pure function — tested
fully offline — and the HTTP poll + sink are injected so the loop never needs a
running engine or network in tests.
"""
from __future__ import annotations

from utah.integrations import engine_bridge as eb


def _flat():
    return {"active_trade": None, "trades_today": 0, "session_pnl": 0.0}


def _in_trade(direction="LONG", entry=7250.0, trades=1):
    return {"active_trade": {"direction": direction, "entry": entry,
                             "stop": 7244.0, "target": 7266.0},
            "trades_today": trades, "session_pnl": 0.0}


def test_open_detected_on_flat_to_trade():
    fires = eb.detect_fires("bible", _flat(), _in_trade())
    kinds = [f["kind"] for f in fires]
    assert "open" in kinds
    o = next(f for f in fires if f["kind"] == "open")
    assert o["engine"] == "bible" and o["direction"] == "LONG" and o["entry"] == 7250.0


def test_close_detected_on_trade_to_flat():
    cur = _flat()
    cur["trades_today"] = 1
    cur["session_pnl"] = 120.0
    fires = eb.detect_fires("apex", _in_trade(), cur)
    kinds = [f["kind"] for f in fires]
    assert "close" in kinds
    assert next(f for f in fires if f["kind"] == "close")["session_pnl"] == 120.0


def test_no_fire_when_state_unchanged_flat():
    assert eb.detect_fires("bible", _flat(), _flat()) == []


def test_no_fire_while_trade_held_open():
    # same active trade across two frames → no new open
    assert eb.detect_fires("bible", _in_trade(), _in_trade()) == []


def test_count_up_without_open_close_still_counts():
    # trades_today jumped but no active_trade transition (fast scalp between polls)
    prev = _flat()
    cur = _flat()
    cur["trades_today"] = 2
    fires = eb.detect_fires("research", prev, cur)
    assert any(f["kind"] == "count_up" for f in fires)


def test_first_frame_no_prev_never_fires():
    # prev is None (cold start): record state, emit nothing
    assert eb.detect_fires("bible", None, _in_trade()) == []


def test_trades_summary_maps_to_state_for_older_builds():
    # apex-style /api/trades summary → a trade_state frame; new completed trades count up
    s = eb._trades_to_state({"total": 9, "total_pnl": 310.0})
    assert s == {"active_trade": None, "trades_today": 9, "session_pnl": 310.0}
    fires = eb.detect_fires("apex", {"active_trade": None, "trades_today": 8}, s)
    assert any(f["kind"] == "count_up" and f["trades_today"] == 9 for f in fires)


def test_poll_records_each_fire_once(tmp_path):
    """poll() diffs a fetched frame against in-memory prev and sinks each fire once."""
    frames = {"bible": _flat()}

    def fetch(key, port):
        return frames[key]

    sunk = []
    bridge = eb.EngineBridge(roster=[("bible", "Apex Signal Bible", 8400)],
                             fetch=fetch, sink=sunk.append)
    bridge.poll()                       # first frame: flat, no fire
    frames["bible"] = _in_trade()       # engine opened a trade
    bridge.poll()                       # → one open
    bridge.poll()                       # held → nothing new
    opens = [e for e in sunk if e["kind"] == "open"]
    assert len(opens) == 1 and opens[0]["engine"] == "bible"
