"""Property resolver — bridge a probate decedent (NAME + COUNTY) to the REAL property
they owned (address / parcel / point), so the 3-mile-radius comp check has an anchor.

This is the hard, honest part. Georgia has 159 counties and NO free statewide
owner→parcel API: Regrid needs a paid token, qPublic (Schneider) blocks bots, and only
SOME counties publish an open ArcGIS parcel FeatureServer with an owner field. So this
resolver is TIERED and GATED: it queries a per-county ArcGIS registry where one exists,
and otherwise records an HONEST gate (available=False) — it NEVER fabricates an address.
Coverage grows by adding counties to ``COUNTY_ARCGIS``. The fetch is injectable so the
logic is unit-tested offline (same convention as probate.py / leads.py).
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from utah import failures
from utah.integrations import maps

log = logging.getLogger("utah.product.property")

#: Per-county open ArcGIS parcel FeatureServers that expose an owner field. Each entry:
#: ``county: {"url": <FeatureServer/0/query>, "owner_field": <col>, "addr_field": <col>}``.
#: Seed verified counties here; everything else gates honestly. (Lower-cased county keys.)
COUNTY_ARCGIS: dict[str, dict[str, str]] = {
    # Harris County, GA — open GARC (Georgia Assoc. of Regional Commissions) hub layer
    # behind the county's official Data HUB. Polygon parcels w/ Owner + PhisicalAddress.
    # LIVE-VERIFIED 2026-06-07: UPPER(Owner) LIKE '%SMITH%' -> "SMITH AARON JR & ANITA K",
    # "906 Cedar Street", parcel 001A008, geom near -85.17,32.87 (Harris Co GA). 21,044 parcels.
    "harris": {
        "url": "https://services1.arcgis.com/Ug5xGQbHsD8zuZzM/arcgis/rest/services/"
               "Parcels4_2026_HUB/FeatureServer/0/query",
        "owner_field": "Owner",
        "addr_field": "PhisicalAddress",
        "parcel_field": "PARCEL_NO",
    },
    # The rest live-verified 2026-06-07 (owner-LIKE '%SMITH%' returned real owner+situs).
    # All polygon layers, so resolve gets address (parcel too) and enrich GEOCODES the
    # address for lat/lng (geometry x/y is None on polygons). Covers 59 of 64 probate rows.
    "houston": {  # Middle GA Regional Commission hub; 'LASTNAME' holds the full owner string
        "url": "https://services1.arcgis.com/Ug5xGQbHsD8zuZzM/arcgis/rest/services/"
               "HoustonCoParcels_withOwner/FeatureServer/0/query",
        "owner_field": "LASTNAME", "addr_field": "ADDRESS", "parcel_field": "PARCEL_NO",
    },
    "bulloch": {  # StaGIS (stabull.org) MapServer/0 — situs FULL_ADDRE
        "url": "https://stabull.org/server/rest/services/Bulloch_Parcels/MapServer/0/query",
        "owner_field": "LASTNAME", "addr_field": "FULL_ADDRE", "parcel_field": "PARCEL_NO",
    },
    "effingham": {  # Effingham County GIS AGOL org; current-year Parcels2024
        "url": "https://services.arcgis.com/9scQWTgPOi3GxJRr/arcgis/rest/services/"
               "Parcels2024/FeatureServer/0/query",
        "owner_field": "LASTNAME", "addr_field": "StreetAdd", "parcel_field": "PARCEL_NO",
    },
    "hall": {  # Hall County GIS server, GeneralTab MapServer/1
        "url": "https://hallgis.hallcounty.org/arcgis/rest/services/GeneralTab/MapServer/1/query",
        "owner_field": "OWNER", "addr_field": "SITE_LOCATION", "parcel_field": "PIN",
    },
    "bryan": {  # Bryan County GIS server, Parcels MapServer/0
        "url": "https://bryangis.bryan-county.org/arcgis/rest/services/Parcels/MapServer/0/query",
        "owner_field": "LASTNAME", "addr_field": "STREET_NAM", "parcel_field": "PIN",
    },
    "forsyth": {  # Forsyth County EnerGov parcel+address MapServer/1
        "url": "https://geo.forsythco.com/gis/rest/services/EnerGov/"
               "EnerGovParcelAddressMapService/MapServer/1/query",
        "owner_field": "OWNERNME1", "addr_field": "SITEADDRESS", "parcel_field": "PARCELID",
    },
}


def _arcgis_query(url: str, where: str, fetch=None, timeout: int = 20) -> dict:
    """One ArcGIS FeatureServer query → parsed JSON (injectable for tests)."""
    if fetch is not None:
        return fetch(url, where)
    full = url + "?" + urllib.parse.urlencode({
        "where": where, "outFields": "*", "f": "json",
        "returnGeometry": "true", "resultRecordCount": "5"})
    req = urllib.request.Request(full, headers={"User-Agent": "Utah/1.0 property"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def resolve_property(case_name: str, county: str, *, fetch=None) -> dict:
    """Decedent NAME + COUNTY → ``{available, source, address?, parcel?, lat?, lng?}``.

    Queries the county's open ArcGIS parcel layer for an owner LIKE the decedent name.
    Honest gate (``available=False`` + recorded) when the county has no open source or the
    owner isn't found — NEVER a fabricated address. ``fetch`` injectable for offline tests."""
    cty = (county or "").strip().lower()
    reg = COUNTY_ARCGIS.get(cty)
    if fetch is None and reg is None:
        failures.record("property", "no_source",
                        f"{case_name[:40]} ({cty}): no open parcel source for this county "
                        "(add an ArcGIS FeatureServer to COUNTY_ARCGIS) — gated, not faked")
        return {"available": False, "gated": True, "county": cty, "source": "none"}
    reg = reg or {"url": "INJECTED", "owner_field": "OWNER", "addr_field": "SITEADDR"}
    name = (case_name or "").strip().upper().replace("'", "")
    of, af = reg["owner_field"], reg["addr_field"]
    # Parcel owner fields store the name "LAST FIRST MIDDLE" but a probate decedent is
    # "FIRST MIDDLE LAST" — a single LIKE on the full string never matches across that
    # reorder. Require the SURNAME and the GIVEN name to BOTH appear (order-agnostic).
    toks = [t for t in name.replace(",", " ").split() if len(t) > 1]
    if len(toks) >= 2:
        surname, given = toks[-1], toks[0]
        where = f"UPPER({of}) LIKE '%{surname}%' AND UPPER({of}) LIKE '%{given}%'"
    else:
        where = f"UPPER({of}) LIKE '%{name}%'"
    try:
        d = _arcgis_query(reg["url"], where, fetch=fetch)
        feats = d.get("features") or []
        if not feats:
            failures.record("property", "owner_not_found",
                            f"{case_name[:40]} ({cty}): no parcel matched owner — gated, not faked")
            return {"available": True, "gated": False, "county": cty, "source": "arcgis",
                    "address": None, "parcel": None}
        attrs = feats[0].get("attributes") or {}
        geom = feats[0].get("geometry") or {}
        # Point layers give x/y; the registered counties are POLYGON layers (rings, no x/y)
        # → lat/lng stay None here and enrich() geocodes the situs address for the point.
        lat = geom.get("y") if "y" in geom else None
        lng = geom.get("x") if "x" in geom else None
        pf = reg.get("parcel_field", "")
        parcel = (attrs.get(pf) if pf else None) or attrs.get("PARCELID") or attrs.get("PARID")
        return {"available": True, "gated": False, "county": cty, "source": "arcgis",
                "address": attrs.get(af), "parcel": parcel,
                "owner": attrs.get(of), "lat": lat, "lng": lng}
    except Exception as exc:  # noqa: BLE001
        failures.record("property", "arcgis_failed", f"{case_name[:40]} ({cty}): {exc}")
        return {"available": False, "gated": False, "county": cty, "error": str(exc)}


def radius_check(lat, lng, radius_m: int = maps.THREE_MILES_M, *, places_fetch=None,
                 smb_fetch=None) -> dict:
    """The 3-mile-radius check around a resolved property point: Maps places within the
    radius PLUS real no-website SMBs from OSM in the bounding box. Both already proven."""
    from utah.product import leads
    out: dict = {"radius_m": radius_m, "places": [], "no_website_smbs": []}
    near = maps.nearby(lat, lng, radius_m, fetch=places_fetch)
    if near.get("available"):
        out["places"] = near.get("places", [])
    # OSM no-website SMBs in the ~radius bbox (deg: 3mi ≈ 0.0435° lat / lng÷cos(lat))
    import math
    dlat = radius_m / 111_320.0
    dlng = radius_m / (111_320.0 * max(0.1, math.cos(math.radians(lat))))
    bbox = (lat - dlat, lng - dlng, lat + dlat, lng + dlng)
    try:
        out["no_website_smbs"] = [s["name"] for s in
                                  leads.find_no_website_smbs(bbox, fetch=smb_fetch)][:15]
    except Exception as exc:  # noqa: BLE001
        failures.record("property", "smb_radius_failed", str(exc))
    return out


def enrich(case_name: str, county: str, *, fetch=None, geocode_fetch=None,
           places_fetch=None, smb_fetch=None) -> dict:
    """Full enrichment for one decedent: resolve property → geocode (if needed) → 3-mile
    radius. Returns the heir_contact payload (or an honest unresolved marker). Never fakes."""
    prop = resolve_property(case_name, county, fetch=fetch)
    if not prop.get("available") or not prop.get("address"):
        return {"property": "unresolved", "county": county,
                "reason": prop.get("source", prop.get("error", "gated"))}
    lat, lng = prop.get("lat"), prop.get("lng")
    if lat is None or lng is None:
        geo = maps.geocode(f"{prop['address']}, {county} County, GA", fetch=geocode_fetch)
        if geo.get("available") and geo.get("lat") is not None:
            lat, lng = geo["lat"], geo["lng"]
    payload = {"address": prop.get("address"), "parcel": prop.get("parcel"),
               "owner": prop.get("owner"), "lat": lat, "lng": lng, "source": prop.get("source")}
    if lat is not None and lng is not None:
        payload["comps"] = radius_check(lat, lng, places_fetch=places_fetch, smb_fetch=smb_fetch)
    return payload


def enrich_ledger(limit: int = 25, *, ledger=None, fetch=None, geocode_fetch=None,
                  places_fetch=None, smb_fetch=None) -> dict:
    """Walk probate rows that don't yet have a resolved address, enrich each (property →
    geocode → 3-mile radius), and write the result onto the row via ledger.update_probate.
    Degrades honestly per row (one bad county never aborts the run). Returns counts. This
    is the cron entry for com.utah.probate-enrich."""
    import psycopg

    from utah import config
    from utah.product.ledger import Ledger

    lg = ledger or Ledger()
    with psycopg.connect(config.DB_DSN, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT case_name, county FROM probate WHERE NOT (heir_contact ? 'address') "
            "LIMIT %s", (limit,)).fetchall()
    resolved = gated = 0
    for case_name, county in rows:
        try:
            payload = enrich(case_name, county, fetch=fetch, geocode_fetch=geocode_fetch,
                             places_fetch=places_fetch, smb_fetch=smb_fetch)
            lg.update_probate(case_name, county, heir_contact=payload)
            resolved += 1 if payload.get("address") else 0
            gated += 0 if payload.get("address") else 1
        except Exception as exc:  # noqa: BLE001 — one bad row must not abort the pass
            failures.record("property", "enrich_row_failed", f"{case_name[:40]}: {exc}")
    log.info("probate enrich: scanned=%d resolved=%d gated=%d", len(rows), resolved, gated)
    return {"scanned": len(rows), "resolved": resolved, "gated": gated}


__all__ = ["resolve_property", "radius_check", "enrich", "enrich_ledger", "COUNTY_ARCGIS"]
