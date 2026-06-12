"""External data — REAL weather/quote fetchers (free, keyless sources) behind the
same honest opt-in gate, proven with zero network.

The old module was a skeleton whose "real" fetcher could only raise — with the flag
present every call failed (documented-as-live). These tests lock the genuine path:
Open-Meteo geocode → forecast for weather, Stooq CSV for quotes, every HTTP hop
bounded, validation and honest failures everywhere else.
"""
from __future__ import annotations

import json

from utah import failures
from utah.integrations import external
from tests.fakes import FakeFailureStore


def _config(tmp_path, monkeypatch, payload) -> None:
    p = tmp_path / "external.json"
    p.write_text(json.dumps(payload) if isinstance(payload, dict) else payload)
    monkeypatch.setattr(external, "EXTERNAL_CREDS", p)


# --- source_available is a REAL gate, not a stat() ----------------------------
def test_source_unavailable_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(external, "EXTERNAL_CREDS", tmp_path / "nope.json")
    assert external.source_available() is False


def test_source_unavailable_when_garbled_or_empty(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, "{broken")
    assert external.source_available() is False
    _config(tmp_path, monkeypatch, {})
    assert external.source_available() is False


def test_source_respects_enabled_false_kill_switch(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, {"enabled": False})
    assert external.source_available() is False
    _config(tmp_path, monkeypatch, {"enabled": True})
    assert external.source_available() is True


# --- weather: geocode → forecast ------------------------------------------------
def _wire_weather(monkeypatch, geo, forecast, calls):
    def fake_get_json(url, params=None):
        calls.append((url, params or {}))
        return geo if "geocoding" in url else forecast
    monkeypatch.setattr(external, "_get_json", fake_get_json)


def test_weather_geocodes_then_fetches_current(tmp_path, monkeypatch):
    failures.set_store(FakeFailureStore())
    _config(tmp_path, monkeypatch, {"enabled": True})
    calls: list = []
    _wire_weather(monkeypatch,
                  geo={"results": [{"name": "Newnan", "admin1": "Georgia",
                                    "country_code": "US",
                                    "latitude": 33.38, "longitude": -84.8}]},
                  forecast={"current": {"temperature_2m": 78.1, "apparent_temperature": 80.0,
                                        "relative_humidity_2m": 60, "wind_speed_10m": 4.2,
                                        "precipitation": 0.0, "weather_code": 1}},
                  calls=calls)
    r = external.weather("Newnan GA")
    assert r["available"] is True and r["gated"] is False
    assert r["data"]["temp_f"] == 78.1 and r["data"]["place"] == "Newnan"
    # geocode first (carries the query), then forecast (carries the coordinates)
    assert "geocoding" in calls[0][0] and calls[0][1]["name"] == "Newnan GA"
    assert calls[1][1]["latitude"] == 33.38 and calls[1][1]["longitude"] == -84.8


def test_weather_no_geocode_match_is_an_honest_failure(tmp_path, monkeypatch):
    store = FakeFailureStore(); failures.set_store(store)
    _config(tmp_path, monkeypatch, {"enabled": True})
    calls: list = []
    _wire_weather(monkeypatch, geo={"results": []}, forecast={}, calls=calls)
    r = external.weather("Xyzzyville Nowhere")
    assert r["available"] is False and r["gated"] is False
    assert "no geocode match" in r["error"]
    assert any("weather_failed" in row[2] for row in store.rows)
    assert len(calls) == 1                       # forecast was never asked for a ghost town


def test_weather_forecast_missing_current_is_an_error(tmp_path, monkeypatch):
    store = FakeFailureStore(); failures.set_store(store)
    _config(tmp_path, monkeypatch, {"enabled": True})
    _wire_weather(monkeypatch,
                  geo={"results": [{"name": "X", "latitude": 1, "longitude": 2}]},
                  forecast={"current": {}}, calls=[])
    r = external.weather("X")
    assert r["available"] is False and "current" in r["error"]


def test_weather_empty_location_never_fetches(monkeypatch):
    failures.set_store(FakeFailureStore())
    called = []
    r = external.weather("   ", fetch=lambda loc: called.append(loc) or {})
    assert r["available"] is False and "required" in r["error"]
    assert called == []


# --- quotes: stooq CSV -----------------------------------------------------------
_CSV = ("Symbol,Date,Time,Open,High,Low,Close,Volume\n"
        "AAPL.US,2026-06-11,22:00:07,201.5,204.1,200.9,203.22,48123456\n")


def test_quote_parses_stooq_csv(tmp_path, monkeypatch):
    failures.set_store(FakeFailureStore())
    _config(tmp_path, monkeypatch, {"enabled": True})
    seen = {}

    def fake_get_text(url, params=None):
        seen["url"], seen["params"] = url, params or {}
        return _CSV

    monkeypatch.setattr(external, "_get_text", fake_get_text)
    r = external.quote("AAPL")
    assert r["available"] is True and r["gated"] is False
    assert r["data"]["close"] == 203.22 and r["data"]["symbol"] == "AAPL.US"
    assert seen["params"]["s"] == "aapl.us"      # bare US tickers get the .us suffix


def test_quote_nd_close_is_an_honest_failure(tmp_path, monkeypatch):
    store = FakeFailureStore(); failures.set_store(store)
    _config(tmp_path, monkeypatch, {"enabled": True})
    nd = _CSV.replace("203.22", "N/D")
    monkeypatch.setattr(external, "_get_text", lambda url, params=None: nd)
    r = external.quote("FAKETICKER")
    assert r["available"] is False and r["gated"] is False
    assert "FAKETICKER" in r["error"]
    assert any("quote_failed" in row[2] for row in store.rows)


def test_quote_empty_symbol_never_fetches(monkeypatch):
    failures.set_store(FakeFailureStore())
    called = []
    r = external.quote("", fetch=lambda s: called.append(s) or {})
    assert r["available"] is False and "required" in r["error"]
    assert called == []


# --- transport is bounded --------------------------------------------------------
def test_http_helpers_pass_a_timeout(monkeypatch):
    seen = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        seen["url"] = req.full_url
        return FakeResp()

    monkeypatch.setattr(external.urllib.request, "urlopen", fake_urlopen)
    out = external._get_json("https://example.test/api", {"q": "x"})
    assert out == {"ok": True}
    assert seen["timeout"] and 0 < seen["timeout"] <= 60
    assert seen["url"].endswith("?q=x")

    text = external._get_text("https://example.test/csv")
    assert text == '{"ok": true}'
    assert seen["timeout"] and 0 < seen["timeout"] <= 60
