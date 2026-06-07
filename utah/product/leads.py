"""Leads capability — local SMBs with no website (the pitch: "I'll build your site").

Ace's lead_scout transitions HERE as a capability behind the brain — not an agent.
It queries OpenStreetMap (Overpass) for shop/craft/amenity businesses tagged WITHOUT a
``website``, drops national chains (which slip in untagged but are useless for a
website-build pitch), and writes each survivor to the product ledger
(``record_lead`` → Postgres, never-twice, pushes the ``leads`` deck channel).

Grounded by construction: every lead is a real OSM business, never fabricated. The
Overpass fetch is an injectable boundary so the logic is unit-tested offline.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
from typing import Callable

log = logging.getLogger("utah.product.leads")

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
)
USER_AGENT = "Utah/1.0 leads-smb (educational)"
QUERY_TIMEOUT_S = 30
HTTP_TIMEOUT_S = 60.0

#: Coweta County, GA — Newnan/Sharpsburg/Senoia (south, west, north, east). Michael's
#: region; a single Overpass query over a much larger box times out (tiling is a TODO).
COWETA_BBOX = (33.20, -84.95, 33.55, -84.55)

#: A metro frontier: tile a large bbox into Overpass-sized sub-boxes.
#: Atlanta metro ring around Coweta; ~0.7deg box tiled at 0.25deg ~= 9 tiles.
METRO_BBOX = (33.0, -85.1, 34.1, -84.0)
TILE_STEP = 0.25  # degrees; each tile small enough to not time out Overpass

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
    """Split (south, west, north, east) into a grid of <=step sub-boxes.

    Pure function. Last row/column clamps to the parent edge so the whole
    box is covered with no overlap and no spill.
    """
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
    """Overpass QL: every node/way in *bbox* in a target category WITHOUT a website."""
    south, west, north, east = bbox
    parts: list[str] = []
    for k, v in _CATEGORIES:
        sel = f'["{k}"]' if v == "*" else f'["{k}"="{v}"]'
        box = f"({south},{west},{north},{east})"
        parts.append(f'node{sel}["name"]["website"!~"."]{box};')
        parts.append(f'way{sel}["name"]["website"!~"."]{box};')
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


def find_no_website_smbs(bbox: tuple[float, float, float, float],
                         fetch: Fetch | None = None) -> list[dict]:
    """Real OSM businesses in *bbox* with no website, national chains removed.
    Each: ``{name, kind, phone}``. Never fabricated — straight from OSM."""
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
        phone = (tags.get("phone") or tags.get("contact:phone") or "").strip()
        out.append({"name": name, "kind": kind, "phone": phone})
    return out


def scout(ledger, bbox: tuple[float, float, float, float] = COWETA_BBOX,
          region: str = "Coweta County, GA", fetch: Fetch | None = None) -> dict:
    """Find no-website SMBs and write each to the product ledger (never-twice).
    Returns ``{found, new, region}``. The ledger push lights the deck per new lead."""
    found = find_no_website_smbs(bbox, fetch)
    new = 0
    for s in found:
        contact = {"phone": s["phone"]} if s["phone"] else None
        if ledger.record_lead(s["name"], s["kind"], region, "osm", contact=contact):
            new += 1
    log.info("leads scout %s: found=%d new=%d", region, len(found), new)
    return {"found": len(found), "new": new, "region": region}


def scout_frontier(ledger, bbox=METRO_BBOX, region="Atlanta Metro Ring",
                   fetch=None, max_tiles=12):
    """Scale leads: tile a metro bbox, scout each tile, dedup at the ledger.

    Each tile is a separate Overpass query (avoids the single-big-box timeout).
    Dedup is the ledger's UNIQUE(name, region) — same lead across tiles counts once.
    Returns {tiles_scanned, found, new, region}.
    """
    tiles = frontier_tiles(bbox)[:max_tiles]
    found = 0
    new = 0
    for tile in tiles:
        res = scout(ledger, bbox=tile, region=region, fetch=fetch)
        found += res["found"]
        new += res["new"]
    return {"tiles_scanned": len(tiles), "found": found, "new": new, "region": region}


__all__ = ["is_national_chain", "build_query", "find_no_website_smbs", "scout",
           "scout_frontier", "frontier_tiles", "COWETA_BBOX", "METRO_BBOX", "TILE_STEP",
           "NATIONAL_CHAINS"]
