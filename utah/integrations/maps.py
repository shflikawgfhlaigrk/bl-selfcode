"""Maps capability — geocode an address + find places within a radius. GATED on the
Google Maps key (``~/.utah/secrets/maps.json`` → ``{"api_key": "..."}``). Fetch is
injectable; with no key each call documents the gate and returns ``available=False`` —
never fabricates a coordinate. Used by probate property enrichment (geocode a resolved
owner address, then the 3-mile-radius comp search) and reusable anywhere a point/radius
is needed. Both endpoints are live-verified against the real key (Geocoding + Places-New).
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from utah import failures
from utah.daemon import runtime

log = logging.getLogger("utah.integrations.maps")

MAPS_CREDS = runtime.UTAH_HOME / "secrets" / "maps.json"
_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
_TEXT_URL = "https://places.googleapis.com/v1/places:searchText"
#: 3 miles in metres — the probate "3-mile radius check".
THREE_MILES_M = 4828
#: Google Places hard ceiling — a circle radius above 50 km is a guaranteed 400.
_RADIUS_MAX_M = 50_000


def source_available() -> bool:
    return MAPS_CREDS.exists()


def _key() -> str:
    """API key from the creds file — '' on missing/garbled/non-dict content so a bad
    secrets file reads as a gate upstream, never a crash mid-request."""
    try:
        raw = json.loads(MAPS_CREDS.read_text())
    except (OSError, ValueError):
        return ""
    return str(raw.get("api_key") or "") if isinstance(raw, dict) else ""


def _gate(kind: str) -> dict:
    failures.record("maps", "gated", f"{kind} gated: no Maps key at {MAPS_CREDS}")
    return {"available": False, "gated": True}


def _clamp_radius(radius_m) -> int:
    """Clamp to Google's documented [1, 50 km] circle bounds — an oversize radius is a
    guaranteed 400, a zero/negative one a guaranteed INVALID_REQUEST."""
    try:
        r = int(radius_m)
    except (TypeError, ValueError):
        return THREE_MILES_M
    return max(1, min(r, _RADIUS_MAX_M))


def _http_json(url: str, *, params=None, data=None, headers=None, timeout=20) -> dict:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def geocode(address: str, *, fetch=None) -> dict:
    """Address → ``{available, lat, lng, formatted}``. Honest gate with no key; honest
    MISS (available True but lat None) when Google resolves nothing — never invents a point."""
    if fetch is None and not source_available():
        return _gate(f"geocode {address[:40]}")
    try:
        if fetch is not None:
            d = fetch(address)
        else:
            key = _key()
            if not key:                      # creds file present but no usable key
                return _gate(f"geocode {address[:40]}")
            d = _http_json(_GEOCODE_URL, params={"address": address, "key": key})
        results = d.get("results") or []
        if not results:
            return {"available": True, "gated": False, "lat": None, "lng": None,
                    "formatted": None, "status": d.get("status")}
        loc = results[0]["geometry"]["location"]
        return {"available": True, "gated": False, "lat": loc["lat"], "lng": loc["lng"],
                "formatted": results[0].get("formatted_address")}
    except Exception as exc:  # noqa: BLE001
        failures.record("maps", "geocode_failed", f"{address[:40]}: {exc}")
        return {"available": False, "gated": False, "error": str(exc)}


def nearby(lat, lng, radius_m: int = THREE_MILES_M, included_types=None, *,
           fetch=None, max_results: int = 15) -> dict:
    """Places within ``radius_m`` of ``(lat,lng)`` → ``{available, places:[{name,address,
    lat,lng}]}``. Default radius = 3 miles. Places API (New) searchNearby POST."""
    if fetch is None and not source_available():
        return _gate(f"nearby {lat},{lng}")
    try:
        radius_m = _clamp_radius(radius_m)
        if fetch is not None:
            d = fetch(lat, lng, radius_m)
        else:
            key = _key()
            if not key:                      # creds file present but no usable key
                return _gate(f"nearby {lat},{lng}")
            body = json.dumps({
                "includedTypes": included_types or ["restaurant", "store"],
                "maxResultCount": max_results,
                "locationRestriction": {"circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": float(radius_m)}},
            }).encode()
            d = _http_json(_NEARBY_URL, data=body, headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": key,
                "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
            })
        places = []
        for p in (d.get("places") or []):
            loc = p.get("location") or {}
            places.append({"name": (p.get("displayName") or {}).get("text", ""),
                           "address": p.get("formattedAddress", ""),
                           "lat": loc.get("latitude"), "lng": loc.get("longitude")})
        return {"available": True, "gated": False, "places": places, "radius_m": radius_m}
    except Exception as exc:  # noqa: BLE001
        failures.record("maps", "nearby_failed", f"{lat},{lng}: {exc}")
        return {"available": False, "gated": False, "error": str(exc)}


def text_search(text_query: str, *, lat: float | None = None, lng: float | None = None,
                radius_m: int = 50000, region_code: str = "US", max_results: int = 20,
                fetch=None) -> dict:
    """Google Places Text Search → ``{available, places:[{name,address,phone,website,types}]}``.
    Used to scout local trades (handyman, plumber, …) with phone numbers; ``website`` is
    included so callers can filter no-website SMBs. Enterprise field mask (phone + website)."""
    if fetch is None and not source_available():
        return _gate(f"text_search {text_query[:40]}")
    try:
        radius_m = _clamp_radius(radius_m)
        if fetch is not None:
            d = fetch(text_query, lat, lng, radius_m)
        else:
            key = _key()
            if not key:                      # creds file present but no usable key
                return _gate(f"text_search {text_query[:40]}")
            body: dict = {"textQuery": text_query, "regionCode": region_code,
                          "pageSize": max_results}
            if lat is not None and lng is not None:
                body["locationBias"] = {"circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": float(radius_m)}}
            d = _http_json(_TEXT_URL, data=json.dumps(body).encode(), headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": key,
                "X-Goog-FieldMask": (
                    "places.displayName,places.formattedAddress,places.nationalPhoneNumber,"
                    "places.websiteUri,places.types,places.location"),
            })
        places = []
        for p in (d.get("places") or []):
            loc = p.get("location") or {}
            places.append({
                "name": (p.get("displayName") or {}).get("text", ""),
                "address": p.get("formattedAddress", ""),
                "phone": (p.get("nationalPhoneNumber") or "").strip(),
                "website": (p.get("websiteUri") or "").strip(),
                "types": p.get("types") or [],
                "lat": loc.get("latitude"), "lng": loc.get("longitude"),
            })
        return {"available": True, "gated": False, "places": places, "query": text_query}
    except Exception as exc:  # noqa: BLE001
        failures.record("maps", "text_search_failed", f"{text_query[:40]}: {exc}")
        return {"available": False, "gated": False, "error": str(exc)}


__all__ = ["geocode", "nearby", "text_search", "source_available", "MAPS_CREDS", "THREE_MILES_M"]
