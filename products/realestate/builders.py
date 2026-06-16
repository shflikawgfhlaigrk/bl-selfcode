"""Real Estate — find builders/contractors in an area (and within 3 miles of a property).

The Real Estate spec needs "ways to find builders in the areas" + "the 3-mile thing." Builders
are just a market, so this is thin orchestration over the proven Black Label Leads any-market
finder (:mod:`utah.product.leads`) — no new scraper, all the OSM/dedup/chain-filtering reused.
The 3-mile radius is the default for "builders near this property".
"""
from __future__ import annotations

from utah.product import leads

#: ~3 miles in km — the default radius for "builders near a property" (the spec's 3-mile thing).
THREE_MILES_KM = 4.83

#: The construction trades treated as "builders" — each is a known market in the leads finder.
BUILDER_MARKETS = ["builder", "contractor", "roofer"]


def find_builders(bbox, *, markets=None, fetch=None) -> list[dict]:
    """Builders/contractors in *bbox*, merged across :data:`BUILDER_MARKETS` and de-duplicated
    by name. Each: ``{name, kind, contact}`` — straight from OSM via the leads finder."""
    out: list[dict] = []
    seen: set[str] = set()
    for market in (markets or BUILDER_MARKETS):
        for b in leads.find_market_smbs(bbox, market, fetch=fetch):
            key = b["name"].lower()
            if key not in seen:
                seen.add(key)
                out.append(b)
    return out


def find_builders_near(lat: float, lon: float, *, radius_km: float = THREE_MILES_KM,
                       markets=None, fetch=None) -> list[dict]:
    """Builders within *radius_km* (default ~3 miles) of a point — e.g. a probate property."""
    return find_builders(leads.bbox_around(lat, lon, radius_km), markets=markets, fetch=fetch)


def scout_builders(ledger, location: str, *, radius_km: float = THREE_MILES_KM,
                   geocoder=None, fetch=None) -> dict:
    """Geocode *location*, find builders within *radius_km*, and record each (never-twice).
    Returns ``{found, new, location, region, geocoded}``; honest (found=0) on a geocode miss."""
    coord = (geocoder or leads._default_geocoder)(location)
    if not coord:
        return {"found": 0, "new": 0, "location": location, "geocoded": False}
    found = find_builders_near(coord[0], coord[1], radius_km=radius_km, fetch=fetch)
    region = f"builders — {location}"
    new = sum(1 for b in found
              if ledger.record_lead(b["name"], b["kind"], region, "osm", contact=b.get("contact")))
    return {"found": len(found), "new": new, "location": location,
            "region": region, "geocoded": True}


__all__ = ["THREE_MILES_KM", "BUILDER_MARKETS", "find_builders",
           "find_builders_near", "scout_builders"]
