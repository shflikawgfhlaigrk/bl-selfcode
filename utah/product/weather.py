"""Weather capability — free, grounded, cached (the R-weather migrate target).

A quick fact, not a model question: Open-Meteo (no key) → one grounded line,
cached on disk for ≤2 h (the spec's "cache <2h"). The fetch, the clock, and the
cache file are injectable so the capability is tested fully offline. A fetch
failure is documented to the AUDIT log and degrades honestly — a marked-stale
cached reading if one exists, else "I don't know." — **never** a fabricated forecast.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date
from typing import Callable

from utah import config, failures

log = logging.getLogger("utah.product.weather")

#: WMO weather-interpretation codes → words (Open-Meteo's ``weather_code``).
_WMO: dict[int, str] = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "rime fog",
    51: "light drizzle", 53: "drizzle", 55: "dense drizzle",
    56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "rain showers", 81: "rain showers", 82: "violent rain showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}

Fetcher = Callable[..., dict]


def _fetch(lat: float, lon: float, timeout: int = 15) -> dict:
    """The real Open-Meteo round-trip (free, no API key)."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m"
        "&temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "utah/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _describe(data: dict, label: str) -> str:
    """One grounded line from the Open-Meteo ``current`` block. All numbers real."""
    cur = data.get("current") or {}
    sky = _WMO.get(int(cur.get("weather_code", -1)), "unknown conditions")
    parts = [f"{label}: {sky}"]
    temp = cur.get("temperature_2m")
    if temp is not None:
        parts.append(f"{round(temp)}°F")
    hum = cur.get("relative_humidity_2m")
    if hum is not None:
        parts.append(f"{round(hum)}% humidity")
    wind = cur.get("wind_speed_10m")
    if wind is not None:
        parts.append(f"wind {round(wind)} mph")
    precip = cur.get("precipitation")
    if precip:
        parts.append(f"{precip} in precipitation")
    return ", ".join(parts) + "."


def _read_cache(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _write_cache(path: str, obj: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        os.replace(tmp, path)   # atomic
    except OSError as exc:
        log.warning("weather cache write failed (non-fatal): %s", exc)


def current(
    *,
    fetch: Fetcher = _fetch,
    now: Callable[[], float] = time.time,
    cache_path: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    label: str | None = None,
) -> str:
    """Current weather as one grounded line. Serves a <2 h cached reading when
    fresh; otherwise fetches and re-caches. Never raises; never fabricates."""
    lat = config.WEATHER_LAT if lat is None else lat
    lon = config.WEATHER_LON if lon is None else lon
    label = config.WEATHER_LABEL if label is None else label
    cache_path = config.WEATHER_CACHE_PATH if cache_path is None else cache_path

    cached = _read_cache(cache_path)
    fresh = (
        cached is not None
        and cached.get("lat") == lat
        and cached.get("lon") == lon
        and (now() - float(cached.get("fetched_at", 0))) < config.WEATHER_CACHE_SECONDS
    )
    if fresh:
        return cached["text"]

    try:
        text = _describe(fetch(lat, lon), label)
    except Exception as exc:  # noqa: BLE001 — any fetch/parse failure degrades honestly
        failures.record("weather", "fetch_failed", str(exc))
        if cached is not None:
            return f"{cached['text']} (cached; live fetch failed)"
        return "I don't know — weather is unavailable right now."

    _write_cache(cache_path, {"fetched_at": now(), "lat": lat, "lon": lon, "text": text})
    return text


# --------------------------------------------------------------------------
# Forecast — "will it rain tomorrow", "weather this week".
# --------------------------------------------------------------------------

#: A query wants the forecast (not the current reading) when it names a future window.
_FORECAST_Q = re.compile(
    r"\b(tomorrow|forecast|this week|next (few )?days|coming days|next week|"
    r"weekend|later (today|this week)|outlook)\b",
    re.I,
)


def _fetch_daily(lat: float, lon: float, timeout: int = 15) -> dict:
    """The real Open-Meteo daily round-trip (free, no API key)."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        "&temperature_unit=fahrenheit&timezone=auto&forecast_days=4"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "utah/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _weekday(iso: str | None) -> str:
    try:
        d = date.fromisoformat(iso)  # type: ignore[arg-type]
        return d.strftime("%a %b ") + str(d.day)
    except (ValueError, TypeError):
        return "that day"


def _day(daily: dict, i: int) -> tuple[str, str | None] | None:
    """One forecast day as ``(body, iso_date)`` — or ``None`` if absent. All real."""
    codes = daily.get("weather_code") or []
    if i >= len(codes):
        return None
    parts = [_WMO.get(int(codes[i]), "unknown conditions")]
    for key, fmt in (
        ("temperature_2m_max", "high {}°F"),
        ("temperature_2m_min", "low {}°F"),
        ("precipitation_probability_max", "{}% chance of precip"),
    ):
        vals = daily.get(key) or []
        if i < len(vals) and vals[i] is not None:
            parts.append(fmt.format(round(vals[i])))
    times = daily.get("time") or []
    return ", ".join(parts), (times[i] if i < len(times) else None)


def _describe_forecast(daily: dict, label: str, query: str) -> str:
    """Render tomorrow (default) or a multi-day outlook from the daily block."""
    if not re.search(r"\btomorrow\b", query or "", re.I):
        lines = [f"{label} forecast:"]
        for i in range(1, 4):  # tomorrow + the two days after
            d = _day(daily, i)
            if d is None:
                break
            body, iso = d
            lines.append(f"{_weekday(iso)}: {body}.")
        return "\n".join(lines) if len(lines) > 1 else \
            "I don't know — the forecast is unavailable right now."
    d = _day(daily, 1)
    if d is None:
        return "I don't know — the forecast is unavailable right now."
    return f"Tomorrow in {label}: {d[0]}."


def _forecast_cache_path(current_path: str) -> str:
    head, tail = os.path.split(current_path)
    return os.path.join(head or ".", "weather_forecast.json")


def forecast(
    query: str,
    *,
    fetch: Fetcher = _fetch_daily,
    now: Callable[[], float] = time.time,
    cache_path: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    label: str | None = None,
) -> str:
    """The forecast as grounded text. Caches the raw daily block (≤2 h) so one
    fetch serves both "tomorrow" and "this week". Never raises; never fabricates."""
    lat = config.WEATHER_LAT if lat is None else lat
    lon = config.WEATHER_LON if lon is None else lon
    label = config.WEATHER_LABEL if label is None else label
    cache_path = _forecast_cache_path(config.WEATHER_CACHE_PATH) if cache_path is None else cache_path

    cached = _read_cache(cache_path)
    fresh = (
        cached is not None
        and cached.get("daily")
        and cached.get("lat") == lat
        and cached.get("lon") == lon
        and (now() - float(cached.get("fetched_at", 0))) < config.WEATHER_CACHE_SECONDS
    )
    if fresh:
        return _describe_forecast(cached["daily"], label, query)

    try:
        daily = (fetch(lat, lon).get("daily")) or {}
        text = _describe_forecast(daily, label, query)
    except Exception as exc:  # noqa: BLE001 — any fetch/parse failure degrades honestly
        failures.record("weather", "forecast_failed", str(exc))
        if cached is not None and cached.get("daily"):
            return _describe_forecast(cached["daily"], label, query) + " (cached; live fetch failed)"
        return "I don't know — the forecast is unavailable right now."

    _write_cache(cache_path, {"fetched_at": now(), "lat": lat, "lon": lon, "daily": daily})
    return text


def answer(query: str, **kwargs) -> str:
    """Dispatch a weather query to the forecast (future window) or current reading."""
    if _FORECAST_Q.search(query or ""):
        return forecast(query, **kwargs)
    return current(**kwargs)


__all__ = ["current", "forecast", "answer"]
