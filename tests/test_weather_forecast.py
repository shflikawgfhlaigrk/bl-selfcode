"""Weather forecast: an extension of the weather capability — "will it rain
tomorrow", "weather this week". Same free Open-Meteo source, same ≤2h cache,
same honest-on-failure contract. The fetch + clock + cache are injectable."""
from __future__ import annotations

from utah.product import weather

# an Open-Meteo-shaped daily block (today + 2 days out)
_DAILY = {
    "daily": {
        "time": ["2026-06-06", "2026-06-07", "2026-06-08"],
        "weather_code": [2, 61, 95],
        "temperature_2m_max": [86.0, 82.4, 79.1],
        "temperature_2m_min": [70.2, 68.5, 66.0],
        "precipitation_probability_max": [10, 60, 80],
    }
}


def _fetch_daily(lat, lon, timeout=15):
    return _DAILY


def test_tomorrow_reports_the_next_day(tmp_path):
    text = weather.forecast("will it rain tomorrow", fetch=_fetch_daily,
                            now=lambda: 1000.0, cache_path=str(tmp_path / "f.json"),
                            label="Gulf Shores, AL")
    assert "Tomorrow" in text
    assert "Gulf Shores, AL" in text
    assert "rain" in text.lower()          # weather_code 61 = rain
    assert "82" in text and "68" in text   # high / low rounded
    assert "60%" in text                   # precip probability


def test_multiday_outlook_lists_several_days(tmp_path):
    text = weather.forecast("what's the weather this week", fetch=_fetch_daily,
                            now=lambda: 1000.0, cache_path=str(tmp_path / "f.json"))
    # a multi-day outlook names more than one upcoming day
    assert text.count("\n") >= 1
    assert "thunder" in text.lower()       # weather_code 95 on day 3


def test_forecast_caches_like_current(tmp_path):
    cache = str(tmp_path / "f.json")
    calls = {"n": 0}

    def counting(lat, lon, timeout=15):
        calls["n"] += 1
        return _DAILY

    weather.forecast("weather tomorrow", fetch=counting, now=lambda: 1000.0, cache_path=cache)
    weather.forecast("weather tomorrow", fetch=counting, now=lambda: 1500.0, cache_path=cache)
    assert calls["n"] == 1                 # second call served from cache


def test_forecast_failure_is_honest(tmp_path):
    def boom(lat, lon, timeout=15):
        raise RuntimeError("network down")

    text = weather.forecast("weather tomorrow", fetch=boom, now=lambda: 1000.0,
                            cache_path=str(tmp_path / "f.json"))
    assert "I don't know" in text and "unavailable" in text


def test_answer_dispatches_current_vs_forecast(tmp_path, monkeypatch):
    calls = {"current": 0, "forecast": 0}
    monkeypatch.setattr(weather, "current", lambda **k: calls.__setitem__("current", 1) or "CURRENT")
    monkeypatch.setattr(weather, "forecast", lambda q, **k: calls.__setitem__("forecast", 1) or "FORECAST")
    assert weather.answer("what's the weather right now") == "CURRENT"
    assert weather.answer("will it rain tomorrow") == "FORECAST"
    assert weather.answer("weather this week") == "FORECAST"
