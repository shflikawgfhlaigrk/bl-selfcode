"""Weather cache hardening — current()/forecast() promise "never raises", so a
hand-edited or torn cache file (missing payload key, garbage fetched_at) must read
as STALE, not crash the capability."""
from __future__ import annotations

import json

from utah import failures
from utah.product import weather
from tests.fakes import FakeFailureStore


def _ok_fetch(lat, lon, timeout=15):
    return {"current": {"weather_code": 0, "temperature_2m": 70.0}}


def _cfg_point():
    from utah import config
    return config.WEATHER_LAT, config.WEATHER_LON


def test_fresh_looking_cache_without_text_refetches_not_keyerror(tmp_path):
    failures.set_store(FakeFailureStore())
    lat, lon = _cfg_point()
    cache = tmp_path / "weather.json"
    cache.write_text(json.dumps({"lat": lat, "lon": lon, "fetched_at": 10_000.0}))
    out = weather.current(fetch=_ok_fetch, now=lambda: 10_060.0,
                          cache_path=str(cache))
    assert "70°F" in out                       # refetched — the torn cache was ignored


def test_torn_cache_and_dead_fetch_degrade_honestly(tmp_path):
    failures.set_store(FakeFailureStore())
    lat, lon = _cfg_point()
    cache = tmp_path / "weather.json"
    cache.write_text(json.dumps({"lat": lat, "lon": lon, "fetched_at": 10_000.0}))

    def dead(lat, lon, timeout=15):
        raise OSError("network down")

    out = weather.current(fetch=dead, now=lambda: 10_060.0, cache_path=str(cache))
    assert out.startswith("I don't know")      # no fabricated reading off a torn cache


def test_garbage_fetched_at_reads_as_stale_never_raises(tmp_path):
    failures.set_store(FakeFailureStore())
    lat, lon = _cfg_point()
    cache = tmp_path / "weather.json"
    cache.write_text(json.dumps({"lat": lat, "lon": lon,
                                 "fetched_at": "yesterday-ish", "text": "old reading."}))
    out = weather.current(fetch=_ok_fetch, now=lambda: 10_060.0, cache_path=str(cache))
    assert "70°F" in out                       # treated stale -> live refetch


def test_forecast_garbage_fetched_at_never_raises(tmp_path):
    failures.set_store(FakeFailureStore())
    lat, lon = _cfg_point()
    cache = tmp_path / "weather_forecast.json"
    cache.write_text(json.dumps({"lat": lat, "lon": lon, "fetched_at": [1, 2],
                                 "daily": {"weather_code": [0, 1],
                                           "time": ["2026-06-12", "2026-06-13"]}}))

    def daily_fetch(lat, lon, timeout=15):
        return {"daily": {"weather_code": [0, 63], "temperature_2m_max": [80, 75],
                          "time": ["2026-06-12", "2026-06-13"]}}

    out = weather.forecast("rain tomorrow?", fetch=daily_fetch,
                           now=lambda: 10_060.0, cache_path=str(cache))
    assert "rain" in out.lower()               # refetched daily block, no crash
