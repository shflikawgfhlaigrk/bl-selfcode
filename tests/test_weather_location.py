"""The weather capability must answer for the place NAMED in the query — not a
hardcoded home town. "weather in Atlanta" geocodes Atlanta; a bare "weather" falls
back to home; an unfindable place degrades to an honest "I don't know" (never a
fabricated reading for the wrong city). Geocode + fetch are injected — zero network."""
from __future__ import annotations

import pytest

from utah import config
from utah.product import weather

_CUR = {
    "current": {
        "temperature_2m": 72.4,
        "relative_humidity_2m": 65,
        "precipitation": 0.0,
        "weather_code": 2,
        "wind_speed_10m": 8.3,
    }
}

_ATLANTA = (33.749, -84.388, "Atlanta, Georgia")


def test_named_city_is_geocoded_not_home(tmp_path):
    seen = {}

    def geo(name):
        seen["name"] = name
        return _ATLANTA

    def fetch(lat, lon, timeout=15):
        seen["lat"], seen["lon"] = lat, lon
        return _CUR

    text = weather.answer(
        "what's the weather in Atlanta",
        geocode=geo, fetch=fetch, now=lambda: 1000.0,
        cache_path=str(tmp_path / "w.json"),
    )
    assert seen["name"].strip().lower() == "atlanta"
    assert (round(seen["lat"]), round(seen["lon"])) == (34, -84)  # Atlanta, not Gulf Shores
    assert "Atlanta" in text
    assert "Gulf Shores" not in text


def test_city_before_the_word_weather(tmp_path):
    seen = {}

    def geo(name):
        seen["name"] = name
        return _ATLANTA

    weather.answer(
        "Atlanta weather", geocode=geo, fetch=lambda *a, **k: _CUR,
        now=lambda: 1000.0, cache_path=str(tmp_path / "w.json"),
    )
    assert seen["name"].strip().lower() == "atlanta"


def test_multiword_city(tmp_path):
    seen = {}

    def geo(name):
        seen["name"] = name
        return (40.7, -74.0, "New York, New York")

    weather.answer(
        "what's the temperature in New York right now",
        geocode=geo, fetch=lambda *a, **k: _CUR,
        now=lambda: 1000.0, cache_path=str(tmp_path / "w.json"),
    )
    assert seen["name"].strip().lower() == "new york"


def test_no_city_defaults_to_home_without_geocoding(tmp_path):
    seen = {}

    def geo(name):
        seen["called"] = True
        return None

    def fetch(lat, lon, timeout=15):
        seen["lat"] = lat
        return _CUR

    text = weather.answer(
        "what's the weather", geocode=geo, fetch=fetch,
        now=lambda: 1000.0, cache_path=str(tmp_path / "w.json"),
    )
    assert "called" not in seen                  # no place named → no geocode
    assert seen["lat"] == config.WEATHER_LAT      # home default
    assert "Gulf Shores" in text


def test_unknown_place_is_honest_not_fabricated(tmp_path):
    fetched = {"n": 0}

    def geo(name):
        return None                               # geocoder finds nothing

    def fetch(lat, lon, timeout=15):
        fetched["n"] += 1
        return _CUR

    text = weather.answer(
        "weather in Narnia", geocode=geo, fetch=fetch,
        now=lambda: 1000.0, cache_path=str(tmp_path / "w.json"),
    )
    assert "I don't know" in text and "Narnia" in text
    assert fetched["n"] == 0                       # never fetched a wrong-city reading


@pytest.mark.parametrize("query", [
    "what's the weather like in Atlanta",
    "what is the weather like in Atlanta today",
    "how's the weather like in Atlanta right now",
    "weather today in Atlanta",
    "what's the weather gonna be in Atlanta",
    "what's the weather going to be in Atlanta tomorrow",
])
def test_filler_or_timeword_before_city_still_geocodes_city(query, tmp_path):
    """A filler ('like', 'gonna be') or time word sitting BETWEEN the verb and the
    city must not swallow the place — this was the Atlanta -> Gulf Shores home-default
    bug ('what's the weather like in Atlanta' returned the config home town)."""
    seen = {}

    def geo(name):
        seen["name"] = name
        return _ATLANTA

    text = weather.answer(
        query, geocode=geo, fetch=lambda *a, **k: _CUR,
        now=lambda: 1000.0, cache_path=str(tmp_path / "w.json"),
    )
    assert seen.get("name", "").strip().lower() == "atlanta", f"{query!r} did not geocode Atlanta"
    assert "Gulf Shores" not in text


def test_courtesy_pronoun_is_not_a_place(tmp_path):
    """'what's the weather for me' must NOT treat 'me' as a city — home default."""
    seen = {}

    def geo(name):
        seen["called"] = name
        return None

    text = weather.answer(
        "what's the weather for me", geocode=geo, fetch=lambda *a, **k: _CUR,
        now=lambda: 1000.0, cache_path=str(tmp_path / "w.json"),
    )
    assert "called" not in seen          # "me" is never geocoded as a place
    assert "Gulf Shores" in text          # home default


def test_named_city_forecast_routes_with_its_coords(tmp_path):
    seen = {}

    def geo(name):
        seen["name"] = name
        return _ATLANTA

    def fetch_daily(lat, lon, timeout=15):
        seen["lat"], seen["lon"] = lat, lon
        return {"daily": {"weather_code": [0, 1], "time": ["2026-06-14", "2026-06-15"],
                          "temperature_2m_max": [90, 91], "temperature_2m_min": [70, 71]}}

    text = weather.answer(
        "forecast for Atlanta tomorrow",
        geocode=geo, fetch=fetch_daily, now=lambda: 1000.0,
        cache_path=str(tmp_path / "wf.json"),
    )
    assert seen["name"].strip().lower() == "atlanta"
    assert (round(seen["lat"]), round(seen["lon"])) == (34, -84)
    assert "Atlanta" in text
