"""Leads capability — local SMBs with no website (the pitch: "I'll build your site").

Ace's lead_scout transitions HERE as a capability behind the brain — not an agent.
It queries OpenStreetMap (Overpass) for shop/craft/amenity businesses tagged WITHOUT a
website, drops national chains, enriches each with name + contact (phone/email/address),
and writes survivors to the product ledger (``record_lead`` → Postgres, never-twice).

For sustained volume (≥500 NEW/day) it scans a MOVING FRONTIER: a large tiled region
with a persistent cursor that advances to FRESH tiles each run, so dedup doesn't just
re-scan the same exhausted geography. Grounded by construction: every lead is a real
OSM business, never fabricated. The Overpass fetch is injectable so the logic is
unit-tested offline.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
from pathlib import Path
from typing import Callable

from utah.daemon import runtime

log = logging.getLogger("utah.product.leads")

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
)
USER_AGENT = "Utah/1.0 leads-smb (educational)"
QUERY_TIMEOUT_S = 30
HTTP_TIMEOUT_S = 60.0

#: Coweta County, GA — Michael's home region (kept for targeted single-county scans).
COWETA_BBOX = (33.20, -84.95, 33.55, -84.55)

#: Legacy metro ring (12 tiles) — kept for back-compat / small scans.
METRO_BBOX = (33.0, -85.1, 34.1, -84.0)

#: The MOVING-FRONTIER region: a large Southeast box (all of GA + AL + edges of the
#: neighbors). Tiled at TILE_STEP into ~300+ Overpass-sized sub-boxes; a persistent
#: cursor advances through FRESH tiles each daily run so ≥500 new/day is sustainable for
#: WEEKS before it wraps (and a wrap still finds genuinely-new/newly-untagged businesses).
#: Widen the box for even more supply.
FRONTIER_BBOX = (30.8, -85.7, 35.0, -81.0)
TILE_STEP = 0.25  # degrees; each tile small enough to not time out Overpass

#: Daily floor + per-run safety cap on tiles. The cap is generous so even a sparse
#: rural sweep (~7 new/tile) can still clear the 500 floor (~72 tiles); dense metro
#: sweeps hit it in <10 tiles and stop early.
DAILY_TARGET = 500
MAX_TILES_PER_RUN = 80
#: Persistent frontier cursor — which tile index to resume from next run.
FRONTIER_STATE = runtime.RUN_DIR / "leads-frontier.json"

#: High-signal "local business that probably can't build its own site" categories.
_CATEGORIES: list[tuple[str, str]] = [
    ("shop", "*"), ("craft", "*"),
    ("amenity", "restaurant"), ("amenity", "cafe"), ("amenity", "bar"),
    ("amenity", "pub"), ("amenity", "fast_food"), ("amenity", "car_wash"),
    ("amenity", "veterinary"), ("leisure", "fitness_centre"),
    ("office", "company"), ("office", "estate_agent"),
    ("office", "insurance"), ("office", "accountant"),
]

#: National chains OSM often leaves untagged for ``website`` (so they leak into the
#: no-website list) but are useless for a website-build pitch. Single-word names match
#: EXACTLY ("shell" keeps "Shell Crafts Boutique"); multiword names match as a prefix.
NATIONAL_CHAINS: frozenset[str] = frozenset({
    "waffle house", "dollar general", "family dollar", "dollar tree", "captain ds",
    "gamestop", "goodwill", "jersey mikes", "subway", "mcdonalds", "burger king",
    "wendys", "taco bell", "kfc", "popeyes", "chick fil a", "dominos", "pizza hut",
    "papa johns", "little caesars", "hardees", "arbys", "sonic", "zaxbys",
    "bojangles", "chipotle", "starbucks", "dunkin", "dunkin donuts", "wingstop",
    "five guys", "walmart", "walgreens", "cvs", "rite aid", "autozone",
    "o reilly auto parts", "advance auto parts", "napa auto parts", "tractor supply",
    "publix", "kroger", "aldi", "save a lot", "food depot", "the fresh market",
    "circle k", "shell", "chevron", "exxon", "bp", "quiktrip", "racetrac",
    "flash foods", "marathon", "citgo", "verizon", "att", "t mobile",
    "metro by t mobile", "cricket wireless", "boost mobile", "h r block",
    "edward jones", "state farm", "allstate", "anytime fitness", "planet fitness",
    "snap fitness", "great clips", "supercuts", "dollar store", "firehouse subs",
    "jimmy johns", "moes", "krystal", "checkers", "dairy queen", "ace hardware",
    "true value", "firestone", "jiffy lube", "valvoline", "meineke", "midas",
    "pep boys", "take 5 oil change", "ntb", "mavis", "christian brothers automotive",
    "aamco", "maaco", "big o tires", "discount tire", "tires plus", "caliber collision",
    "gerber collision", "o reilly", "advance auto", "carquest", "titlemax", "title max",
    "titlebucks", "title bucks", "advance america", "check into cash", "check n go",
    "world finance", "cash america", "speedy cash", "first cash pawn", "ezpawn",
    "onemain financial", "regional finance", "republic finance", "security finance",
    "covington credit", "lendmark", "rent a center", "aarons", "buddys home furnishings",
    "rent king", "ez pawn", "cricket", "metro pcs", "metropcs", "ubreakifix",
    "aspen dental", "gnc", "vitamin shoppe", "the joint chiropractic",
    "american family care", "sally beauty", "sport clips", "fantastic sams", "ulta",
    "sephora", "crunch fitness", "orangetheory", "wells fargo", "bank of america",
    "regions bank", "regions", "truist", "suntrust", "synovus", "ameris bank",
    "united community bank", "pnc bank", "chase bank", "wells fargo bank", "big lots",
    "hibbett sports", "hibbett", "rainbow", "cato", "citi trends", "shoe show",
    "shoe carnival", "rack room shoes", "ollies", "ross dress for less", "tj maxx",
    "marshalls", "burlington", "five below", "harbor freight", "batteries plus",
    "sherwin williams", "ppg paints", "mattress firm", "ashley furniture",
    "rooms to go", "badcock", "tuesday morning", "golden corral", "ihop", "dennys",
    "long john silvers", "churchs chicken", "cookout", "cook out", "baskin robbins",
    "cold stone", "edible arrangements", "mcalisters deli", "zoes kitchen",
    "panera bread", "qdoba", "which wich", "steaknshake", "steak n shake",
    "huddle house", "el pollo loco", "valero", "sunoco", "speedway", "wawa", "sheetz",
    "murphy usa", "golden pantry", "kangaroo express", "the pantry", "spectrum",
})

Fetch = Callable[[str], str]  # Overpass QL -> raw JSON text (injectable boundary)


def frontier_tiles(
    bbox: tuple[float, float, float, float], step: float = TILE_STEP
) -> list[tuple[float, float, float, float]]:
    """Split (south, west, north, east) into a grid of <=step sub-boxes (pure).
    Last row/column clamps to the parent edge — full coverage, no overlap/spill."""
    if step <= 0:
        raise ValueError(f"step must be positive, got {step!r}")
    south, west, north, east = bbox
    tiles = []
    s = south
    while s < north:
        n = min(s + step, north)
        w = west
        while w < east:
            e = min(w + step, east)
            tiles.append((round(s, 6), round(w, 6), round(n, 6), round(e, 6)))
            w = e
        s = n
    return tiles


def _normalize_name(name: str) -> str:
    s = (name or "").lower().replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def is_national_chain(name: str) -> bool:
    norm = _normalize_name(name)
    if not norm:
        return False
    for chain in NATIONAL_CHAINS:
        if norm == chain:
            return True
        if " " in chain and norm.startswith(chain + " "):
            return True
    return False


def build_query(bbox: tuple[float, float, float, float]) -> str:
    """Overpass QL: every node/way in *bbox* in a target category with NO web presence.

    "No website" means NONE of ``website`` / ``contact:website`` / ``url`` is set —
    otherwise a business that tags its site under contact:website/url would wrongly
    slip into the no-website list."""
    south, west, north, east = bbox
    no_web = '["website"!~"."]["contact:website"!~"."]["url"!~"."]'
    parts: list[str] = []
    for k, v in _CATEGORIES:
        sel = f'["{k}"]' if v == "*" else f'["{k}"="{v}"]'
        box = f"({south},{west},{north},{east})"
        parts.append(f'node{sel}["name"]{no_web}{box};')
        parts.append(f'way{sel}["name"]{no_web}{box};')
    return f"[out:json][timeout:{QUERY_TIMEOUT_S}];(" + "".join(parts) + ");out tags center;"


def _http_fetch(query: str) -> str:
    """POST the query to Overpass (mirrors on failure). Raises on total failure."""
    last: Exception | None = None
    for url in OVERPASS_URLS:
        try:
            req = urllib.request.Request(
                url, data=query.encode("utf-8"),
                headers={"User-Agent": USER_AGENT, "Content-Type": "text/plain"},
            )
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001 — try the next mirror
            last = exc
            log.warning("overpass mirror failed (%s): %s", url, exc)
    raise RuntimeError(f"all Overpass mirrors failed: {last}")


def _extract_contact(tags: dict) -> dict:
    """Pull phone + email + address from OSM tags into a contact dict (empties dropped)."""
    phone = (tags.get("phone") or tags.get("contact:phone") or "").strip()
    email = (tags.get("email") or tags.get("contact:email") or "").strip()
    street = " ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x)
    address = ", ".join(x for x in (
        street, tags.get("addr:city"), tags.get("addr:state"), tags.get("addr:postcode")
    ) if x).strip()
    contact: dict[str, str] = {}
    if phone:
        contact["phone"] = phone
    if email:
        contact["email"] = email
    if address:
        contact["address"] = address
    return contact


def find_no_website_smbs(bbox: tuple[float, float, float, float],
                         fetch: Fetch | None = None) -> list[dict]:
    """Real OSM businesses in *bbox* with no website, national chains removed.
    Each: ``{name, kind, contact}`` where contact = {phone?, email?, address?}.
    Never fabricated — straight from OSM."""
    raw = (fetch or _http_fetch)(build_query(bbox))
    data = json.loads(raw)
    out: list[dict] = []
    seen: set[str] = set()
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        name = (tags.get("name") or "").strip()
        if not name or is_national_chain(name):
            continue
        if name.lower() in seen:           # node+way dupes of the same business
            continue
        seen.add(name.lower())
        kind = tags.get("shop") or tags.get("craft") or tags.get("amenity") \
            or tags.get("office") or tags.get("leisure") or "business"
        out.append({"name": name, "kind": kind, "contact": _extract_contact(tags)})
    return out


def scout(ledger, bbox: tuple[float, float, float, float] = COWETA_BBOX,
          region: str = "Coweta County, GA", fetch: Fetch | None = None) -> dict:
    """Find no-website SMBs and write each to the product ledger (never-twice).
    Returns ``{found, new, region}``. Each lead carries name + contact (phone/email/addr)."""
    found = find_no_website_smbs(bbox, fetch)
    new = 0
    for s in found:
        contact = s.get("contact") or None
        if ledger.record_lead(s["name"], s["kind"], region, "osm", contact=contact):
            new += 1
    log.info("leads scout %s: found=%d new=%d", region, len(found), new)
    return {"found": len(found), "new": new, "region": region}


def scout_frontier(ledger, bbox=METRO_BBOX, region="Atlanta Metro Ring",
                   fetch=None, max_tiles=12):
    """Legacy fixed-frontier scan (kept for back-compat). Tiles a bbox, scouts each,
    dedup at the ledger. Prefer ``run_scheduled`` (moving frontier) for daily volume."""
    tiles = frontier_tiles(bbox)[:max_tiles]
    found = new = 0
    for tile in tiles:
        res = scout(ledger, bbox=tile, region=region, fetch=fetch)
        found += res["found"]
        new += res["new"]
    return {"tiles_scanned": len(tiles), "found": found, "new": new, "region": region}


# ── moving frontier (persistent cursor) ──────────────────────────────────────
def _load_cursor() -> int:
    try:
        return int(json.loads(FRONTIER_STATE.read_text()).get("i", 0))
    except Exception:  # noqa: BLE001 — absent/corrupt → start at 0
        return 0


def _save_cursor(i: int) -> None:
    try:
        FRONTIER_STATE.parent.mkdir(parents=True, exist_ok=True)
        FRONTIER_STATE.write_text(json.dumps({"i": i}))
    except Exception as exc:  # noqa: BLE001
        log.warning("frontier cursor write failed: %s", exc)


def _tile_region(base: str, tile: tuple[float, float, float, float]) -> str:
    """Per-tile region bucket so dedup (UNIQUE name,region) doesn't false-collide a
    same-named business across different towns — each tile is its own bucket."""
    s, w, _, _ = tile
    return f"{base} [{s:.2f},{w:.2f}]"


def run_scheduled(region: str = "Georgia Frontier", target: int = DAILY_TARGET,
                  bbox=FRONTIER_BBOX, max_tiles: int = MAX_TILES_PER_RUN,
                  ledger=None, fetch: Fetch | None = None,
                  cursor_load=None, cursor_save=None) -> dict:
    """``com.utah.leads`` cron entry — MOVING FRONTIER, ≥``target`` new/day.

    Advance a persistent cursor through FRESH tiles of a large region, scouting until
    ``target`` NEW leads are recorded (or the per-run tile cap is hit). Persists the
    cursor so each daily run continues into unscanned geography → sustained volume,
    not a re-scan of exhausted tiles. A tile that fails all Overpass mirrors is
    skipped (logged), so one bad tile never aborts the run. Real OSM only — never
    fabricated. Returns ``{tiles_scanned, found, new, target, met, region, cursor}``."""
    if ledger is None:
        from utah.product.ledger import Ledger
        ledger = Ledger()
    load = cursor_load or _load_cursor
    save = cursor_save or _save_cursor

    tiles = frontier_tiles(bbox)
    if not tiles:
        return {"tiles_scanned": 0, "found": 0, "new": 0, "target": target,
                "met": False, "region": region, "cursor": 0}
    start = load() % len(tiles)
    i = start
    found = new = scanned = 0
    while new < target and scanned < max_tiles and scanned < len(tiles):
        tile = tiles[i % len(tiles)]
        try:
            res = scout(ledger, bbox=tile, region=_tile_region(region, tile), fetch=fetch)
            found += res["found"]
            new += res["new"]
        except Exception as exc:  # noqa: BLE001 — a bad tile must not abort the run
            log.warning("leads tile %s failed, skipping: %s", tile, exc)
        scanned += 1
        i = (i + 1) % len(tiles)
    save(i)
    out = {"tiles_scanned": scanned, "found": found, "new": new, "target": target,
           "met": new >= target, "region": region, "cursor": i}
    log.info("leads cron (moving frontier): %s", out)
    return out


__all__ = ["is_national_chain", "build_query", "find_no_website_smbs", "scout",
           "scout_frontier", "frontier_tiles", "run_scheduled", "_extract_contact",
           "COWETA_BBOX", "METRO_BBOX", "FRONTIER_BBOX", "TILE_STEP", "DAILY_TARGET",
           "MAX_TILES_PER_RUN", "FRONTIER_STATE", "NATIONAL_CHAINS"]
