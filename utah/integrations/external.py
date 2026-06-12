"""External-data capability — REAL weather + market-quote lookups behind an honest gate.

Ace's weather/finance/accountant transition here (NOT agents). The old skeleton's
"fetcher" could only raise — with the flag present every call failed
(documented-as-live). These are genuine, OWN-IT sources (free, keyless, nothing to
subscribe to):

- weather: Open-Meteo — geocode the location, then fetch current conditions.
- quote:   Stooq end-of-day CSV — bare US tickers get the ``.us`` suffix.

Gate: ``~/.utah/secrets/external.json`` must parse to a non-empty JSON object with
``enabled`` not false (``{"enabled": true}`` is the minimal opt-in — external HTTP
stays Michael's call). With no usable config each call documents the gate and returns
``available=False`` — never fabricates a number. Every HTTP hop is bounded; fetchers
stay injectable for tests (zero network).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import urllib.parse
import urllib.request

from utah import failures
from utah.daemon import runtime

log = logging.getLogger("utah.integrations.external")

EXTERNAL_CREDS = runtime.UTAH_HOME / "secrets" / "external.json"
#: Bound on every external HTTP hop. Env-tunable, never unbounded.
HTTP_TIMEOUT_S = float(os.environ.get("UTAH_EXTERNAL_HTTP_TIMEOUT_S", "10"))

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
QUOTE_URL = "https://stooq.com/q/l/"
_USER_AGENT = "Utah/1.0 (external-data capability)"


def _load_config() -> dict:
    """Parse the opt-in file; ``{}`` when missing/garbled (a gate, not a crash)."""
    try:
        cfg = json.loads(EXTERNAL_CREDS.read_text())
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def source_available() -> bool:
    """True only for a USABLE opt-in: a non-empty JSON object whose ``enabled`` key
    (default true) isn't false. A bare ``stat()`` would call a garbled file "wired"."""
    cfg = _load_config()
    return bool(cfg) and bool(cfg.get("enabled", True))


# --- bounded transport (injected away in tests) --------------------------------
def _http_get(url: str, params: dict | None = None) -> bytes:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return resp.read()


def _get_json(url: str, params: dict | None = None) -> dict:
    """Bounded GET → parsed-JSON object. Raises on HTTP/parse failure."""
    data = json.loads(_http_get(url, params).decode("utf-8", "replace") or "{}")
    return data if isinstance(data, dict) else {}


def _get_text(url: str, params: dict | None = None) -> str:
    """Bounded GET → text body. Raises on HTTP failure."""
    return _http_get(url, params).decode("utf-8", "replace")


# --- the real fetchers ----------------------------------------------------------
def _fetch_weather(location: str) -> dict:
    """Open-Meteo: geocode *location*, then current conditions in °F/mph.
    Raises when the place doesn't geocode or the reply has no current block."""
    geo = _get_json(GEOCODE_URL, {"name": location, "count": 1})
    hits = geo.get("results") or []
    if not hits:
        raise RuntimeError(f"no geocode match for {location!r}")
    hit = hits[0]
    reply = _get_json(FORECAST_URL, {
        "latitude": hit.get("latitude"), "longitude": hit.get("longitude"),
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                   "wind_speed_10m,precipitation,weather_code",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
    })
    current = reply.get("current") or {}
    if current.get("temperature_2m") is None:
        raise RuntimeError(f"forecast reply missing current conditions for {location!r}")
    return {
        "place": hit.get("name"), "region": hit.get("admin1"),
        "country": hit.get("country_code"),
        "temp_f": current.get("temperature_2m"),
        "feels_like_f": current.get("apparent_temperature"),
        "humidity_pct": current.get("relative_humidity_2m"),
        "wind_mph": current.get("wind_speed_10m"),
        "precip": current.get("precipitation"),
        "weather_code": current.get("weather_code"),
    }


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fetch_quote(symbol: str) -> dict:
    """Stooq CSV quote. Bare tickers get ``.us``; ``N/D`` (Stooq's "no data") raises —
    an unknown symbol must surface as a failure, never as a fabricated price."""
    sym = symbol.strip().lower()
    if "." not in sym:
        sym = f"{sym}.us"
    text = _get_text(QUOTE_URL, {"s": sym, "f": "sd2t2ohlcv", "h": "", "e": "csv"})
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise RuntimeError(f"no quote rows for {symbol!r}")
    row = rows[0]
    close = _num(row.get("Close"))
    if close is None:
        raise RuntimeError(
            f"no quote for {symbol!r} (source said {row.get('Close') or 'nothing'!r})")
    return {
        "symbol": (row.get("Symbol") or sym).upper(),
        "date": row.get("Date"), "time": row.get("Time"),
        "open": _num(row.get("Open")), "high": _num(row.get("High")),
        "low": _num(row.get("Low")), "close": close,
        "volume": _num(row.get("Volume")),
    }


# --- never-raises boundaries ------------------------------------------------------
def _gate(kind: str) -> dict:
    failures.record("external", "gated",
                    f"{kind} gated: external data not opted in at {EXTERNAL_CREDS} "
                    "(Michael's input)")
    return {"available": False, "gated": True}


def weather(location: str, *, fetch=None) -> dict:
    """Current weather for *location*. Honest-gated; never raises."""
    location = (location or "").strip()
    if not location:
        return {"available": False, "gated": False, "error": "location is required"}
    if fetch is None and not source_available():
        return _gate(f"weather {location[:30]}")
    try:
        return {"available": True, "gated": False, "location": location,
                "data": (fetch or _fetch_weather)(location)}
    except Exception as exc:  # noqa: BLE001 — never-raises boundary; callers get an honest result
        failures.record("external", "weather_failed", f"{location[:30]}: {exc}")
        return {"available": False, "gated": False, "error": str(exc)}


def quote(symbol: str, *, fetch=None) -> dict:
    """Market quote for *symbol*. Honest-gated; never raises."""
    symbol = (symbol or "").strip()
    if not symbol:
        return {"available": False, "gated": False, "error": "symbol is required"}
    if fetch is None and not source_available():
        return _gate(f"quote {symbol[:10]}")
    try:
        return {"available": True, "gated": False, "symbol": symbol,
                "data": (fetch or _fetch_quote)(symbol)}
    except Exception as exc:  # noqa: BLE001 — never-raises boundary; callers get an honest result
        failures.record("external", "quote_failed", f"{symbol[:10]}: {exc}")
        return {"available": False, "gated": False, "error": str(exc)}


__all__ = ["weather", "quote", "source_available", "EXTERNAL_CREDS"]
