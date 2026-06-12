"""wc_feed env tunables — the float lane (UTAH_WC_INTERVAL) gets the same
garbage-proof fallback as the int lane, and a junk frame mid-stream feeds nothing
into the BarStream (the hook-survival contract end to end)."""
from __future__ import annotations

import json as _json

from utah.integrations import wc_feed


def test_env_float_parses_and_falls_back(monkeypatch):
    monkeypatch.setenv("UTAH_WC_TEST_FLOAT", "2.5")
    assert wc_feed._env_float("UTAH_WC_TEST_FLOAT", 30.0) == 2.5
    monkeypatch.setenv("UTAH_WC_TEST_FLOAT", "garbage")
    assert wc_feed._env_float("UTAH_WC_TEST_FLOAT", 30.0) == 30.0
    monkeypatch.delenv("UTAH_WC_TEST_FLOAT")
    assert wc_feed._env_float("UTAH_WC_TEST_FLOAT", 30.0) == 30.0


def test_frame_candle_normalizes_into_barstream_path():
    """The exact frame shape the persistent hook consumes: envelope → candle →
    BarStream.feed without any intermediate raise."""
    real = ('{"cmd":"feed","data":{"type":"candle","c":"CM.MNQM6","candle":'
            '{"co":1.0,"cM":3.0,"cm":0.5,"cc":2.0,"cepoch":1781045182,"type":"rt"}}}')
    frame = _json.dumps({"method": "Network.webSocketFrameReceived",
                         "params": {"response": {"payloadData": real}}})
    cd = wc_feed._frame_candle(frame)
    assert cd == {"symbol": "CM.MNQM6", "close": 2.0, "open": 1.0,
                  "high": 3.0, "low": 0.5, "epoch": 1781045182}
    # junk variants around the same envelope stay quiet — the hook never drops
    assert wc_feed._frame_candle(frame[:-30]) is None              # truncated mid-flight
    assert wc_feed._frame_candle(None) is None                     # CDP recv() oddity
    assert wc_feed._frame_candle(
        _json.dumps({"method": "Network.webSocketFrameReceived",
                     "params": {"response": {"payloadData": "{\"cmd\":\"feed\"}"}}})) is None
