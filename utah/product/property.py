"""Property resolver — bridge a probate decedent (NAME + COUNTY) to the REAL property
they owned (address / parcel / point), so the 3-mile nearby-business survey has an anchor.

HONESTY (audit §3.2 — the most important credibility correction): this resolves the
decedent parcel's own COUNTY-ASSESSED / fair-market value (a single number off the parcel
record — NOT a renovation-adjusted "after-repair value") and surveys NEARBY BUSINESSES
within ~3 miles (Maps places + no-website SMBs). It does **not** compute an average of
comparable home SALES — that needs a sold-comps source (ATTOM/Regrid/MLS) we don't have.
``arv`` = the parcel's assessed value; ``nearby`` = the business survey. Never oversold.

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
        "value_field": "Value",          # fair-market value (= FMVRES+FMVACC+land), e.g. 108892
    },
    # The rest live-verified 2026-06-07 (owner-LIKE '%SMITH%' returned real owner+situs).
    # All polygon layers, so resolve gets address (parcel too) and enrich GEOCODES the
    # address for lat/lng (geometry x/y is None on polygons). Covers 59 of 64 probate rows.
    "houston": {  # Middle GA Regional Commission hub; 'LASTNAME' holds the full owner string
        "url": "https://services1.arcgis.com/Ug5xGQbHsD8zuZzM/arcgis/rest/services/"
               "HoustonCoParcels_withOwner/FeatureServer/0/query",
        "owner_field": "LASTNAME", "addr_field": "ADDRESS", "parcel_field": "PARCEL_NO",
        "value_field": "CURR_VAL",       # current appraised value, e.g. 150800
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
        "value_field": "CUR_VALUE",      # current value (land+improvement), e.g. 16500
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


def _to_money(v) -> int | None:
    """County value field → a positive integer dollar amount, or None. Handles '108892',
    150800, '$1,234'; treats 0/blank/garbage as None (never a fabricated $0 ARV)."""
    try:
        n = int(float(str(v).replace(",", "").replace("$", "").strip()))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _ssl_context():
    """A certifi-backed SSL context: under launchd the interpreter's default CA lookup
    proved environment-dependent (Hall Co GIS verified fine interactively but raised
    CERTIFICATE_VERIFY_FAILED in the com.utah.enrich cron, 2026-06-10). certifi pins the
    bundle so verification is deterministic in every context — never disabled."""
    import ssl

    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 — certifi missing: fall back to interpreter default
        return None


def _arcgis_query(url: str, where: str, fetch=None, timeout: int = 20) -> dict:
    """One ArcGIS FeatureServer query → parsed JSON (injectable for tests)."""
    if fetch is not None:
        return fetch(url, where)
    full = url + "?" + urllib.parse.urlencode({
        "where": where, "outFields": "*", "f": "json",
        "returnGeometry": "true", "resultRecordCount": "5"})
    req = urllib.request.Request(full, headers={"User-Agent": "Utah/1.0 property"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


#: ArcGIS parcel layers carry the owner's MAILING address under wildly varying field names.
#: These substrings catch the common ones (the tax-bill address = the heir/owner contact).
_MAIL_STREET_HINTS = ("MAILADD", "MAIL_ADD", "MAILADR", "OWNERADD", "OWNER_ADD", "OWNADDR",
                      "MAILINGADD", "MAIL_STREET", "MAILADDR")
_MAIL_CITY_HINTS = ("MAILCITY", "MAIL_CITY", "OWNERCITY", "OWNER_CITY", "MAILINGCITY")
_MAIL_STATE_HINTS = ("MAILSTATE", "MAIL_STATE", "OWNERSTATE", "OWNER_STATE", "MAILST")
_MAIL_ZIP_HINTS = ("MAILZIP", "MAIL_ZIP", "OWNERZIP", "OWNER_ZIP", "MAILINGZIP", "MAILZIPCODE")


def _pick(attrs: dict, hints: tuple[str, ...]) -> str:
    """First non-empty attr whose UPPER-cased key contains one of *hints*."""
    up = {(k or "").upper(): v for k, v in attrs.items()}
    for hint in hints:
        for key, val in up.items():
            if hint in key and str(val or "").strip() and str(val).strip().lower() != "null":
                return str(val).strip()
    return ""


def _owner_mailing_address(attrs: dict) -> dict:
    """Assemble the owner's mailing address from a parcel record (best-effort across county
    field-name conventions). Returns ``{}`` when no mailing street is present — never faked.
    A street alone is enough to skip-trace; city/state/zip fill in when the layer has them.

    Two conventions live-verified: explicit MAIL*/OWNER* fields (Harris-style), and the
    bare ADDRESS1/2/3 + CITY/STATE/ZIP tax-roll block (Bulloch-style; 2026-06-09 PARRONDO:
    mailing '202 HIGHLAND ROAD, STATESBORO GA 30458' != situs '2255 OLD RIGGS MILL RD')."""
    street = _pick(attrs, _MAIL_STREET_HINTS)
    if not street:
        # Generic tax-roll block: ADDRESS1..3 are the owner's mailing lines (the situs
        # lives in dedicated fields like FULL_ADDRE/ADDRESS on these layers).
        lines = [str(attrs.get(k) or "").strip() for k in ("ADDRESS1", "ADDRESS2", "ADDRESS3")]
        lines = [ln for ln in lines if ln]
        if lines:
            street = lines[-1] if len(lines) == 1 else " ".join(lines)
    if not street:
        return {}
    city = _pick(attrs, _MAIL_CITY_HINTS) or str(attrs.get("CITY") or "").strip()
    state = _pick(attrs, _MAIL_STATE_HINTS) or str(attrs.get("STATE") or "").strip()
    zipc = _pick(attrs, _MAIL_ZIP_HINTS) or str(attrs.get("ZIP") or "").strip()
    line2 = " ".join(p for p in (city, state, zipc) if p).strip()
    return {"street": street, "city": city, "state": state, "zip": zipc,
            "full": (f"{street}, {line2}" if line2 else street)}


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
        vf = reg.get("value_field")
        arv = _to_money(attrs.get(vf)) if vf else None   # county appraised/fair-market value
        return {"available": True, "gated": False, "county": cty, "source": "arcgis",
                "address": attrs.get(af), "parcel": parcel, "arv": arv,
                "owner": attrs.get(of), "lat": lat, "lng": lng,
                # The owner's MAILING address (where tax bills go) — the heir/owner direct-
                # mail contact (often != the property situs). This is the probate last-mile.
                "owner_mail": _owner_mailing_address(attrs)}
    except Exception as exc:  # noqa: BLE001
        failures.record("property", "arcgis_failed", f"{case_name[:40]} ({cty}): {exc}")
        return {"available": False, "gated": False, "county": cty, "error": str(exc)}


def radius_check(lat, lng, radius_m: int = maps.THREE_MILES_M, *, places_fetch=None,
                 smb_fetch=None) -> dict:
    """The 3-mile NEARBY-BUSINESS survey around a resolved property point: Maps places
    within the radius PLUS real no-website SMBs from OSM in the bounding box. This is a
    survey of nearby BUSINESSES — NOT comparable home-sale prices (that would need a
    sold-comps API we don't have). Never oversold as a 'comps' price average."""
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


def _arcgis_stats_query(url: str, params: dict, fetch=None, timeout: int = 20) -> dict:
    """One ArcGIS server-side STATISTICS query → parsed JSON (injectable for tests).
    Separate from ``_arcgis_query`` because stats use geometry+outStatistics params, not a
    WHERE page of feature rows."""
    if fetch is not None:
        return fetch(url, params)
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={"User-Agent": "Utah/1.0 property"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def area_value_avg(county: str, lat, lng, radius_m: int = maps.THREE_MILES_M, *,
                   fetch=None) -> dict:
    """AVERAGE county-assessed parcel value within ``radius_m`` of ``(lat,lng)`` — the
    '3-mile average' the reports were missing (2026-06-10). Computed SERVER-SIDE
    (outStatistics avg+count) on the SAME county layer that produced the parcel's own
    ``arv``, so it is county-assessed values — honest, NOT a sold-comps average. Gates
    when the county has no registered value layer or the server errors; never fabricates."""
    reg = COUNTY_ARCGIS.get((county or "").strip().lower())
    vf = (reg or {}).get("value_field")
    if not reg or not vf:
        return {"available": False, "gated": True, "county": county,
                "reason": "no county value layer"}
    stats_params = {
        "where": "1=1",
        "geometry": json.dumps({"x": float(lng), "y": float(lat),
                                "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryPoint", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "distance": str(int(radius_m)), "units": "esriSRUnit_Meter",
        "outStatistics": json.dumps([
            {"statisticType": "avg", "onStatisticField": vf,
             "outStatisticFieldName": "avg_value"},
            {"statisticType": "count", "onStatisticField": vf,
             "outStatisticFieldName": "n_parcels"}]),
        "returnGeometry": "false", "f": "json",
    }
    try:
        data = _arcgis_stats_query(reg["url"], stats_params, fetch=fetch)
        attrs = (data.get("features") or [{}])[0].get("attributes") or {}
        avg = _to_money(attrs.get("avg_value"))
        if avg is None:
            raise RuntimeError(f"no avg in response: {str(data)[:120]}")
        return {"available": True, "gated": False, "avg_value": avg,
                "parcels": int(attrs.get("n_parcels") or 0), "radius_m": radius_m,
                "field": vf, "method": "server_stats"}
    except Exception as stats_exc:  # noqa: BLE001 — try the bbox-sample fallback below
        last = stats_exc
    # FALLBACK — Hall + Harris layers 400 on ANY outStatistics (live-diagnosed
    # 2026-06-10): fetch the parcel VALUES inside the 3-mile bbox (paged) and average
    # client-side. Same square-bbox approximation radius_check uses for OSM; labeled
    # method=bbox_sample with the real parcel count. Never fabricates.
    import math
    dlat = radius_m / 111_320.0
    dlng = radius_m / (111_320.0 * max(0.1, math.cos(math.radians(float(lat)))))
    values: list[int] = []
    try:
        for page in range(4):                              # ≤4k parcels bounds the run
            data = _arcgis_stats_query(reg["url"], {
                "where": "1=1",
                "geometry": f"{float(lng) - dlng},{float(lat) - dlat},"
                            f"{float(lng) + dlng},{float(lat) + dlat}",
                "geometryType": "esriGeometryEnvelope", "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects", "outFields": vf,
                "returnGeometry": "false", "resultRecordCount": "1000",
                "resultOffset": str(page * 1000), "f": "json"}, fetch=fetch)
            feats = data.get("features") or []
            values += [m for f in feats
                       if (m := _to_money((f.get("attributes") or {}).get(vf))) is not None]
            if len(feats) < 1000 or not data.get("exceededTransferLimit", True):
                break
        if not values:
            raise RuntimeError(f"no parcel values in bbox (stats err: {last})")
        return {"available": True, "gated": False,
                "avg_value": int(sum(values) / len(values)), "parcels": len(values),
                "radius_m": radius_m, "field": vf, "method": "bbox_sample"}
    except Exception as exc:  # noqa: BLE001 — both paths down: gate, never fabricate
        failures.record("property", "area_avg_failed", f"{county}: {exc}")
        return {"available": False, "gated": True, "county": county, "reason": str(exc)}


def enrich(case_name: str, county: str, *, fetch=None, geocode_fetch=None,
           places_fetch=None, smb_fetch=None, stats_fetch=None) -> dict:
    """Full enrichment for one decedent: resolve the PROPERTY (address/parcel/owner/
    assessed-value) → geocode → nearby-business survey. Returns the property payload (or an
    honest unresolved marker). NOTE: this resolves the property, NOT the HEIR's contact —
    heir mailing-address skip-trace is the separate probate-outreach last-mile. Never fakes."""
    prop = resolve_property(case_name, county, fetch=fetch)
    if not prop.get("available") or not prop.get("address"):
        return {"property": "unresolved", "county": county,
                "reason": prop.get("source", prop.get("error", "gated"))}
    lat, lng = prop.get("lat"), prop.get("lng")
    geo: dict = {}
    if lat is None or lng is None:
        geo = maps.geocode(f"{prop['address']}, {county} County, GA", fetch=geocode_fetch)
        if geo.get("available") and geo.get("lat") is not None:
            lat, lng = geo["lat"], geo["lng"]
    payload = {"address": prop.get("address"), "parcel": prop.get("parcel"),
               "owner": prop.get("owner"), "arv": prop.get("arv"),
               "lat": lat, "lng": lng, "source": prop.get("source"),
               # The probate direct-mail target (was the missing key that left every
               # resolved row letter-blocked): the owner's MAILING address when the
               # layer has one, else the geocoded SITUS as an explicit to-property
               # fallback (a county-recorded address, addressed to the owner/estate).
               "owner_mail": prop.get("owner_mail") or {}}
    if not payload["owner_mail"]:
        formatted = geo.get("formatted") or f"{prop['address']}, {county} County, GA"
        payload["situs_mail"] = {"street": prop.get("address"), "city": "", "state": "GA",
                                 "zip": "", "full": formatted, "to_property": True}
    if lat is not None and lng is not None:
        # 'nearby' (renamed from the misleading 'comps'): a nearby-BUSINESS survey, not a
        # comparable-home-sale price average. Honesty fix, audit §3.2.
        payload["nearby"] = radius_check(lat, lng, places_fetch=places_fetch, smb_fetch=smb_fetch)
        # the 3-mile AVERAGE assessed value (county layer, server-side stats) — gates
        # honestly for counties without a value layer; reports render it when available.
        payload["area_avg_3mi"] = area_value_avg(county, lat, lng, fetch=stats_fetch)
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
        # Two classes of work, neither re-ground forever: unresolved rows NOT tried in
        # the last 7 days (the old query re-scanned the same dead rows hourly, starving
        # everything and logging resolved=0 — which read as "fully gated"), plus
        # resolved rows missing the direct-mail target (owner_mail/situs_mail) — the
        # backfill for rows enriched before the mail key existed (2026-06-09).
        rows = conn.execute(
            "SELECT case_name, county FROM probate "
            "WHERE (NOT (heir_contact ? 'address') "
            "       AND coalesce((heir_contact->>'tried_at')::timestamptz, "
            "                    'epoch'::timestamptz) < now() - interval '7 days') "
            "   OR (heir_contact ? 'address' AND NOT heir_contact ? 'owner_mail' "
            "       AND NOT heir_contact ? 'situs_mail') "
            "LIMIT %s", (limit,)).fetchall()
    resolved = gated = 0
    for case_name, county in rows:
        try:
            payload = enrich(case_name, county, fetch=fetch, geocode_fetch=geocode_fetch,
                             places_fetch=places_fetch, smb_fetch=smb_fetch)
            if payload.get("property") == "unresolved":
                # stamp the attempt so a dead row rests 7 days instead of hogging the run
                import datetime as _dt
                payload["tried_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
            lg.update_probate(case_name, county, heir_contact=payload, arv=payload.get("arv"))
            resolved += 1 if payload.get("address") else 0
            gated += 0 if payload.get("address") else 1
        except Exception as exc:  # noqa: BLE001 — one bad row must not abort the pass
            failures.record("property", "enrich_row_failed", f"{case_name[:40]}: {exc}")
    log.info("probate enrich: scanned=%d resolved=%d gated=%d", len(rows), resolved, gated)
    return {"scanned": len(rows), "resolved": resolved, "gated": gated}


def backfill_area_avg(limit: int = 50, *, ledger=None, fetch=None) -> dict:
    """Stamp ``area_avg_3mi`` onto probate rows that were enriched BEFORE the key existed
    (2026-06-10). Cheap targeted pass: one stats query per row, only rows with a resolved
    point; counties without a value layer get the honest gate marker so they rest. Merges
    into the existing heir_contact jsonb (update_probate replaces the column wholesale)."""
    import psycopg

    from utah import config
    from utah.product.ledger import Ledger

    lg = ledger or Ledger()
    with psycopg.connect(config.DB_DSN, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT case_name, county, heir_contact FROM probate "
            "WHERE heir_contact ? 'address' AND NOT heir_contact ? 'area_avg_3mi' "
            "LIMIT %s", (limit,)).fetchall()
    stamped = available = 0
    for case_name, county, hc in rows:
        hc = hc or {}
        lat, lng = hc.get("lat"), hc.get("lng")
        if lat is None or lng is None:
            hc["area_avg_3mi"] = {"available": False, "gated": True, "reason": "no point"}
        else:
            hc["area_avg_3mi"] = area_value_avg(county, lat, lng, fetch=fetch)
        try:
            lg.update_probate(case_name, county, heir_contact=hc)
            stamped += 1
            available += 1 if hc["area_avg_3mi"].get("available") else 0
        except Exception as exc:  # noqa: BLE001 — one bad row must not abort the pass
            failures.record("property", "area_backfill_failed", f"{case_name[:40]}: {exc}")
    log.info("area_avg backfill: stamped=%d available=%d", stamped, available)
    return {"scanned": len(rows), "stamped": stamped, "available": available}


__all__ = ["resolve_property", "radius_check", "area_value_avg", "enrich", "enrich_ledger",
           "backfill_area_avg", "COUNTY_ARCGIS"]
