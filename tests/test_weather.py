"""The weather capability: free, grounded, cached (<2h). The fetch, the clock,
and the cache file are injectable so it is tested fully offline. A fetch failure
is honest (recorded + "unavailable" or marked-stale) — never a fabricated forecast."""
from __future__ import annotations

from utah import config
from utah.product import weather

# an Open-Meteo-shaped current block
_CUR = {
    "current": {
        "temperature_2m": 72.4,
        "relative_humidity_2m": 65,
        "precipitation": 0.0,
        "weather_code": 2,
        "wind_speed_10m": 8.3,
    }
}


def _fetch_ok(lat, lon, timeout=15):
    return _CUR


def test_current_describes_real_values(tmp_path):
    text = weather.current(
        fetch=_fetch_ok, now=lambda: 1000.0, cache_path=str(tmp_path / "w.json"),
        label="Gulf Shores, AL",
    )
    assert "Gulf Shores, AL" in text
    assert "72" in text                 # rounded temperature
    assert "partly cloudy" in text      # WMO code 2
    assert "I don't know" not in text


def test_cache_hit_skips_fetch(tmp_path):
    cache = str(tmp_path / "w.json")
    calls = {"n": 0}

    def counting_fetch(lat, lon, timeout=15):
        calls["n"] += 1
        return _CUR

    t = [1000.0]
    first = weather.current(fetch=counting_fetch, now=lambda: t[0], cache_path=cache)
    t[0] = 1000.0 + config.WEATHER_CACHE_SECONDS - 1   # still inside the window
    second = weather.current(fetch=counting_fetch, now=lambda: t[0], cache_path=cache)
    assert calls["n"] == 1               # only the first call hit the network
    assert first == second


def test_stale_cache_refetches(tmp_path):
    cache = str(tmp_path / "w.json")
    calls = {"n": 0}

    def counting_fetch(lat, lon, timeout=15):
        calls["n"] += 1
        return _CUR

    t = [1000.0]
    weather.current(fetch=counting_fetch, now=lambda: t[0], cache_path=cache)
    t[0] = 1000.0 + config.WEATHER_CACHE_SECONDS + 1   # past the window
    weather.current(fetch=counting_fetch, now=lambda: t[0], cache_path=cache)
    assert calls["n"] == 2


def test_fetch_failure_with_no_cache_is_honest(tmp_path):
    def boom(lat, lon, timeout=15):
        raise RuntimeError("network down")

    text = weather.current(fetch=boom, now=lambda: 1000.0, cache_path=str(tmp_path / "w.json"))
    assert "I don't know" in text and "unavailable" in text


def test_fetch_failure_serves_marked_stale_cache(tmp_path):
    cache = str(tmp_path / "w.json")
    weather.current(fetch=_fetch_ok, now=lambda: 1000.0, cache_path=cache)  # seed cache

    def boom(lat, lon, timeout=15):
        raise RuntimeError("network down")

    t_stale = 1000.0 + config.WEATHER_CACHE_SECONDS + 1
    text = weather.current(fetch=boom, now=lambda: t_stale, cache_path=cache)
    assert "72" in text and "cached" in text.lower()  # stale but real, marked


def test_location_default_is_gulf_shores(tmp_path):
    seen = {}

    def capture_fetch(lat, lon, timeout=15):
        seen["lat"], seen["lon"] = lat, lon
        return _CUR

    weather.current(fetch=capture_fetch, now=lambda: 1000.0, cache_path=str(tmp_path / "w.json"))
    assert seen["lat"] == config.WEATHER_LAT == 30.2460
    assert seen["lon"] == config.WEATHER_LON
