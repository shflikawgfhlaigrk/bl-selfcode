"""Leads capability — local SMBs with no website (the pitch: "I'll build your site").

**SMB pipeline only** — sources ``osm`` and ``google_maps``. Probate heirs are in
``probate`` table / ``probate_motivated`` campaign; they never enter this module.

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
import os
import re
import urllib.request
from pathlib import Path
from typing import Callable

from utah.daemon import runtime
from utah.objects import Lead

log = logging.getLogger("utah.product.leads")

OVERPASS_URLS = (
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
USER_AGENT = "Utah/1.0 leads-smb (educational)"
QUERY_TIMEOUT_S = 30
HTTP_TIMEOUT_S = 60.0
#: Overpass rate-limiting (HTTP 429) is the #1 yield killer on a multi-tile burst.
#: A 429 means "this mirror is throttling you", not "your query is bad" — and the
#: throttle is PER MIRROR, so the fastest recovery is to FAIL OVER to a different
#: mirror immediately (the others usually aren't throttling the same client). We
#: keep ONE quick same-mirror retry (handles a transient blip) then move on, and
#: only after ALL mirrors 429 in a pass do we exponential-backoff and re-sweep them.
#: Measured live: retrying the throttled primary 3× cost ~25s/tile; fast failover to
#: a healthy mirror is ~0.5s/tile. Honest yield = reach a working mirror fast.
OVERPASS_MAX_RETRIES = 1          # quick same-mirror retry before failing over
OVERPASS_BACKOFF_BASE_S = 2.0     # backoff between FULL all-mirror sweeps (× jitter)
OVERPASS_MIRROR_SWEEPS = 4        # how many times to re-sweep all mirrors on total 429
#: Polite pacing between consecutive tile fetches so a long sweep does not trip the
#: rate limiter in the first place (Overpass asks for a gap between heavy requests).
OVERPASS_TILE_PAUSE_S = 1.0

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

#: Multi-region rotation so 500/day is SUSTAINABLE, not a one-pass that silently
#: exhausts. The production cron (bbox=None) walks a single int cursor across the
#: CONCATENATED tiles of every region below — ~2,000 tiles spanning the Southeast —
#: so the frontier keeps finding FRESH geography for years before any wrap. Each
#: region keeps its own per-tile dedup bucket. Widen / append regions for more supply.
FRONTIER_REGIONS: list[tuple[str, tuple[float, float, float, float]]] = [
    ("Georgia Frontier", (30.8, -85.7, 35.0, -81.0)),
    ("Alabama Frontier", (30.2, -88.5, 35.0, -85.0)),
    ("South Carolina Frontier", (32.0, -83.4, 35.2, -78.5)),
    ("Tennessee Frontier", (35.0, -90.3, 36.7, -81.6)),
    ("North Carolina Frontier", (33.8, -84.3, 36.6, -75.5)),
    ("Florida North Frontier", (29.2, -87.6, 31.0, -81.4)),
]

#: Daily floor + per-run safety cap on tiles. The cap is generous so even a sparse
#: rural sweep (~7 new/tile) can still clear the 500 floor (~72 tiles); dense metro
#: sweeps hit it in <10 tiles and stop early.
DAILY_TARGET = 500
MAX_TILES_PER_RUN = 80
#: Persistent frontier cursor — which tile index to resume from next run.
FRONTIER_STATE = runtime.RUN_DIR / "leads-frontier.json"
#: Maps trade-scout cursor (rotates query × metro center each run).
MAPS_SCOUT_STATE = runtime.RUN_DIR / "leads-maps-scout.json"

#: Google Maps text queries for local trades — handyman / home-repair buyers.
MAPS_TRADE_QUERIES: list[str] = [
    "handyman", "general contractor", "home repair", "plumber", "electrician",
    "hvac contractor", "roofing contractor", "landscaping service",
]
#: Handyman-focused queries for the phone pipeline (no website + phone).
MAPS_HANDYMAN_QUERIES: list[str] = [
    "handyman", "handyman services", "home repair", "general contractor",
    "local handyman", "handyman company",
]
#: Metro centers for Maps location bias (name, lat, lng) — Southeast sweep.
MAPS_SCOUT_CENTERS: list[tuple[str, float, float]] = [
    ("Atlanta GA", 33.749, -84.388),
    ("Savannah GA", 32.080, -81.091),
    ("Macon GA", 32.841, -83.632),
    ("Augusta GA", 33.474, -82.010),
    ("Columbus GA", 32.461, -84.988),
    ("Athens GA", 33.951, -83.357),
    ("Newnan GA", 33.380, -84.800),
    ("Marietta GA", 33.953, -84.550),
    ("Alpharetta GA", 34.075, -84.294),
    ("Roswell GA", 34.023, -84.362),
    ("Lawrenceville GA", 33.956, -83.988),
    ("Gainesville GA", 34.298, -83.825),
    ("Valdosta GA", 30.833, -83.279),
    ("Albany GA", 31.579, -84.156),
    ("Rome GA", 34.257, -85.165),
    ("Warner Robins GA", 32.596, -83.652),
    ("Birmingham AL", 33.521, -86.802),
    ("Montgomery AL", 32.379, -86.307),
    ("Mobile AL", 30.695, -88.040),
    ("Huntsville AL", 34.730, -86.586),
    ("Tuscaloosa AL", 33.210, -87.569),
    ("Charlotte NC", 35.227, -80.843),
    ("Raleigh NC", 35.780, -78.639),
    ("Greensboro NC", 36.073, -79.792),
    ("Winston-Salem NC", 36.100, -80.244),
    ("Durham NC", 35.994, -78.898),
    ("Fayetteville NC", 35.053, -78.879),
    ("Wilmington NC", 34.226, -77.945),
    ("Charleston SC", 32.777, -79.931),
    ("Columbia SC", 34.000, -81.035),
    ("Greenville SC", 34.852, -82.394),
    ("Myrtle Beach SC", 33.689, -78.887),
    ("Spartanburg SC", 34.950, -81.932),
    ("Nashville TN", 36.163, -86.781),
    ("Memphis TN", 35.150, -90.049),
    ("Knoxville TN", 35.961, -83.921),
    ("Chattanooga TN", 35.046, -85.309),
    ("Jacksonville FL", 30.332, -81.656),
    ("Tallahassee FL", 30.438, -84.281),
    ("Pensacola FL", 30.421, -87.217),
    ("Louisville KY", 38.253, -85.759),
    ("Lexington KY", 38.040, -84.503),
]
MAPS_SCOUT_RADIUS_M = 45000   # ~28 miles around each center
MAPS_SCOUT_MAX_RESULTS = 20
MAPS_DAILY_TARGET = 40        # cron cap per day (bulk runs pass a higher target)
MAPS_BULK_TARGET = int(os.environ.get("UTAH_MAPS_BULK_TARGET", "3000"))

#: High-signal "local business that probably can't build its own site" categories.
_CATEGORIES: list[tuple[str, str]] = [
    ("shop", "*"), ("craft", "*"),
    ("amenity", "restaurant"), ("amenity", "cafe"), ("amenity", "bar"),
    ("amenity", "pub"), ("amenity", "fast_food"), ("amenity", "car_wash"),
    ("amenity", "veterinary"), ("leisure", "fitness_centre"),
    ("office", "company"), ("office", "estate_agent"),
    ("office", "insurance"), ("office", "accountant"),
]

#: ``shop=*`` is broad — these sub-values are retail AREAS / big-box, not a single
#: owner-operated business that wants a website pitch. Dropped by kind.
_NON_SMB_KINDS: frozenset[str] = frozenset({
    "mall", "department_store", "supermarket", "hypermarket", "wholesale", "marketplace",
})
#: Name shapes that are a place/structure/civic body, not a business with a buyer
#: (e.g. "5 Points Shopping Center", "... Foundation Concessions Building"). Kept tight
#: to avoid dropping real SMBs.
_JUNK_NAME = re.compile(
    r"(shopping\s+cent(?:er|re)|\boutlet\s+mall\b|\bconcession|chamber of commerce"
    r"|home ?owners? assoc|\bhoa\b|\bcity of\b|\bcounty of\b)", re.I)


def _is_pitchable_smb(name: str, kind: str) -> bool:
    """A real local SMB to pitch a website to — not a mall, big-box, or civic POI."""
    return kind not in _NON_SMB_KINDS and not _JUNK_NAME.search(name)

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
    # --- 2026-06-07: chains that were leaking into the no-website list (OSM left
    # the per-location node untagged for website, but the brand obviously has one) ---
    "7 brew", "seven brew", "dutch bros", "scooters coffee", "biggby coffee",
    "pj's coffee", "pjs coffee", "hunt brothers pizza", "chicken salad chick",
    "marcos pizza", "your pie", "mellow mushroom", "taco mac", "hibachi express",
    "longhorn steakhouse", "cracker barrel", "olive garden", "red lobster",
    "outback steakhouse", "texas roadhouse", "carrabbas", "applebees", "chilis",
    "buffalo wild wings", "raising canes", "culvers", "freddys", "jasons deli",
    "panda express", "moe's southwest grill", "newks eatery", "united rentals",
    "sunbelt rentals", "sunbelt rental", "u haul", "uhaul", "penske",
    "enterprise rent a car", "enterprise rent-a-car", "hertz", "budget truck rental",
    "massage envy", "smartstyle", "piggly wiggly", "jcpenney", "jc penney",
    "ethan allen", "floor decor", "floor and decor", "mavis tires brakes",
    "mavis tires and brakes", "mavis discount tire", "hobby lobby", "michaels",
    "petco", "petsmart", "pet supplies plus", "academy sports", "dicks sporting goods",
    "best buy", "home depot", "lowes", "lowe's home improvement", "office depot",
    "officemax", "staples", "the ups store", "ups store", "fedex office",
    "pilot", "pilot travel center", "flying j", "loves travel stop", "loves",
    "walmart supercenter", "walmart garden center", "walmart neighborhood market",
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
    """Overpass QL: every named node/way in *bbox* in a target category.

    Niche-broadening (Michael, 2026-06-09): the old query EXCLUDED businesses with a
    ``website``/``contact:website``/``url`` tag — which capped the emailable pool at
    ~1% of supply. The pitch is niche-agnostic and a tagged website is the PRECISE
    email-enrichment source (scrape their own contact page), so has-website SMBs are
    now captured too; chains are still dropped by name + kind filters."""
    south, west, north, east = bbox
    parts: list[str] = []
    for k, v in _CATEGORIES:
        sel = f'["{k}"]' if v == "*" else f'["{k}"="{v}"]'
        box = f"({south},{west},{north},{east})"
        parts.append(f'node{sel}["name"]{box};')
        parts.append(f'way{sel}["name"]{box};')
    return f"[out:json][timeout:{QUERY_TIMEOUT_S}];(" + "".join(parts) + ");out tags center;"


def _is_rate_limit(exc: Exception) -> bool:
    """True iff *exc* is an Overpass throttle (HTTP 429 / 504 gateway-timeout)."""
    code = getattr(exc, "code", None)
    return code in (429, 504)


def _urlopen(url: str, data: bytes, headers: dict, timeout: float) -> str:
    """Default opener: POST to Overpass, return decoded body. Raises on HTTP error.
    Isolated so tests inject a fake opener and exercise the failover logic offline."""
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _try_mirror(do_open, url, data, headers):
    """One quick same-mirror attempt + ``OVERPASS_MAX_RETRIES`` immediate retries (no
    backoff — handles a transient blip). Returns the body, or raises the last error so
    the caller can fail over to the next mirror. Same-mirror retries are immediate
    because a throttle is PER MIRROR: real recovery comes from switching mirrors, not
    from hammering a throttled one."""
    last: Exception | None = None
    for _ in range(OVERPASS_MAX_RETRIES + 1):
        try:
            return do_open(url, data, headers, HTTP_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not _is_rate_limit(exc):
                raise  # mirror down / bad query → fail over immediately
    raise last  # type: ignore[misc]


def _http_fetch(query: str, *, opener=None, sleep=None) -> str:
    """POST the query to Overpass with real rate-limit failover. Raises on total failure.

    Strategy (measured live: throttled-primary retries cost ~25s/tile, healthy-mirror
    failover ~0.5s/tile): SWEEP the mirror list, giving each one quick attempt; the
    first success returns. A 429 fails over to the NEXT mirror immediately (the throttle
    is per-mirror, so a sibling mirror usually isn't throttling the same client). Only
    when EVERY mirror 429s in a full sweep do we exponential-backoff and re-sweep, up to
    ``OVERPASS_MIRROR_SWEEPS`` times. ``opener``/``sleep`` are injectable so the failover
    + backoff is unit-tested offline (``opener(url, data, headers, timeout) -> body``)."""
    import random
    import time

    do_open = opener or _urlopen
    nap = sleep if sleep is not None else time.sleep
    data = query.encode("utf-8")
    headers = {"User-Agent": USER_AGENT, "Content-Type": "text/plain"}
    last: Exception | None = None
    for sweep in range(OVERPASS_MIRROR_SWEEPS):
        all_throttled = True
        for url in OVERPASS_URLS:
            try:
                return _try_mirror(do_open, url, data, headers)
            except Exception as exc:  # noqa: BLE001 — try the next mirror
                last = exc
                if not _is_rate_limit(exc):
                    all_throttled = False
                log.warning("overpass mirror failed (%s): %s", url, exc)
        if not all_throttled or sweep == OVERPASS_MIRROR_SWEEPS - 1:
            break  # a non-throttle error (mirror down) or out of sweeps → give up
        delay = OVERPASS_BACKOFF_BASE_S * (2 ** sweep) * (1 + random.random())
        log.warning("all overpass mirrors throttled (sweep %d), backing off %.1fs",
                    sweep + 1, delay)
        nap(delay)
    raise RuntimeError(f"all Overpass mirrors failed: {last}")


#: Splits a captured phone string into its first usable number: OSM packs several
#: numbers into one ``phone`` tag (``"+1-770-555-1234;+1-770-555-5678"``) and appends
#: extensions (``" ext 5"`` / ``" x12"``). Concatenating all the digits would yield a
#: value that never dedups, so we keep only what precedes the first separator/extension.
_PHONE_SPLIT = re.compile(r"\s*[;,]\s*|\s+ext\.?\s*|\s+x(?=\d)", re.I)


def normalize_phone(raw: str) -> str:
    """Captured US phone → E.164 ``+1XXXXXXXXXX``; ``""`` if it isn't a valid US number."""
    first = _PHONE_SPLIT.split(raw or "", maxsplit=1)[0]
    digits = re.sub(r"\D", "", first)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "23456789":
        return f"+1{digits}"
    return ""


def _extract_contact(tags: dict) -> dict:
    """Pull phone + email + address + website from OSM tags (empties dropped). The
    website feeds enrich.find_email's precise own-site path."""
    phone = normalize_phone(tags.get("phone") or tags.get("contact:phone") or "")
    email = (tags.get("email") or tags.get("contact:email") or "").strip()
    website = (tags.get("website") or tags.get("contact:website") or tags.get("url") or "").strip()
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
    if website:
        contact["website"] = website
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
        kind = tags.get("shop") or tags.get("craft") or tags.get("amenity") \
            or tags.get("office") or tags.get("leisure") or "business"
        if not _is_pitchable_smb(name, kind):   # mall / big-box / civic POI, not a buyer
            continue
        if name.lower() in seen:           # node+way dupes of the same business
            continue
        seen.add(name.lower())
        # Bind the producer to the typed Lead contract (B4): a field-name typo here is
        # now a Struct error, not a silent dict-key drift that outreach would mis-read.
        out.append(Lead(name=name, kind=kind, contact=_extract_contact(tags),
                        source="osm").as_dict())
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
    except (OSError, ValueError, TypeError) as exc:  # absent/corrupt → start at 0
        log.debug("frontier cursor reset to 0 (%s)", exc)
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


def purge_multi_location_chains(min_locations: int = 3, ledger=None) -> dict:
    """Self-defending chain filter: a name appearing at ``min_locations``+ DISTINCT
    regions is a chain (a genuine local SMB has ONE location). Deletes them — catching the
    chains the hardcoded NATIONAL_CHAINS denylist misses entirely (new brands) or misses
    via normalization gaps ("AT&T"→"at t"≠"att", "O'Reilly"→"oreilly"≠"o reilly"). Called
    at the end of each frontier run so the table self-cleans. BOUNDED (connect_timeout +
    statement_timeout — house DB rule, same pattern as selfcode_web) and honest: a dead
    store degrades to ``{"purged": 0, "error": ...}`` with a recorded failure, never a
    raise out of the cron. Returns ``{purged}``."""
    import psycopg

    from utah import config, failures

    try:
        with psycopg.connect(
                config.DB_DSN, autocommit=True, connect_timeout=8,
                options=f"-c statement_timeout={config.DB_STATEMENT_TIMEOUT_MS}") as c:
            n = c.execute(
                "DELETE FROM leads WHERE name IN (SELECT name FROM leads GROUP BY name "
                "HAVING count(DISTINCT region) >= %s)", (min_locations,)).rowcount or 0
    except psycopg.Error as exc:
        failures.record("leads", "chain_purge_failed", f"chain purge skipped: {exc}")
        log.warning("leads chain purge failed: %s", exc)
        return {"purged": 0, "error": str(exc)}
    if n:
        log.info("leads: purged %d multi-location (chain) rows", n)
    return {"purged": n}


def _all_frontier_tiles() -> list[tuple[str, tuple[float, float, float, float]]]:
    """Every tile of every FRONTIER_REGIONS region, each tagged with its region name.
    The production cursor is a single int into THIS combined list, so advancing rotates
    naturally through GA → AL → SC → TN → NC → FL → (wrap) — thousands of fresh tiles."""
    out: list[tuple[str, tuple[float, float, float, float]]] = []
    for name, box in FRONTIER_REGIONS:
        out.extend((name, t) for t in frontier_tiles(box))
    return out


def run_scheduled(region: str = "Georgia Frontier", target: int = DAILY_TARGET,
                  bbox=None, max_tiles: int = MAX_TILES_PER_RUN,
                  ledger=None, fetch: Fetch | None = None,
                  cursor_load=None, cursor_save=None, foundation_gate=None) -> dict:
    """``com.utah.leads`` cron entry — MOVING, SELF-REPLENISHING FRONTIER, ≥``target`` new/day.

    With ``bbox=None`` (the production default) a single persistent int cursor walks the
    CONCATENATED tiles of every FRONTIER_REGIONS region (~2,000 tiles across the Southeast),
    so each daily run continues into FRESH, unscanned geography and rotates region-to-region
    automatically — 500/day is sustainable for years, not a one-pass that silently dries up.
    Pass an explicit ``bbox`` for a single-region scan (tests/manual). A tile that fails all
    Overpass mirrors is skipped (logged) so one bad tile never aborts the run. Real OSM only,
    never fabricated. Returns ``{tiles_scanned, found, new, target, met, region, cursor}``."""
    from utah import foundation

    gate = foundation.gate_cron if foundation_gate is None else foundation_gate
    skip = gate("leads")
    if skip:
        return skip

    if ledger is None:
        from utah.product.ledger import Ledger
        ledger = Ledger()
    load = cursor_load or _load_cursor
    save = cursor_save or _save_cursor

    # tagged = [(region_name, tile), ...] — combined regions (production) or one (explicit bbox)
    if bbox is None:
        tagged = _all_frontier_tiles()
    else:
        tagged = [(region, t) for t in frontier_tiles(bbox)]
    if not tagged:
        return {"tiles_scanned": 0, "found": 0, "new": 0, "target": target,
                "met": False, "region": region, "cursor": 0}
    start = load() % len(tagged)
    i = start
    found = new = scanned = 0
    hit: set[str] = set()
    # Pace real network sweeps so a long burst doesn't trip Overpass rate-limiting in
    # the first place (the backoff in _http_fetch is the safety net; this is prevention).
    # Tests inject a `fetch`, so they never pause — the suite stays fast & offline.
    paced = fetch is None
    while new < target and scanned < max_tiles and scanned < len(tagged):
        rname, tile = tagged[i % len(tagged)]
        try:
            res = scout(ledger, bbox=tile, region=_tile_region(rname, tile), fetch=fetch)
            found += res["found"]
            new += res["new"]
            hit.add(rname)
        except Exception as exc:  # noqa: BLE001 — a bad tile must not abort the run
            log.warning("leads tile %s failed, skipping: %s", tile, exc)
        scanned += 1
        i = (i + 1) % len(tagged)
        if paced and scanned < max_tiles and new < target:
            import time
            time.sleep(OVERPASS_TILE_PAUSE_S)
    save(i)
    if bbox is None:  # production run — self-clean chains the name denylist missed
        try:
            purge_multi_location_chains()
        except Exception as exc:  # noqa: BLE001 — cleanup must never fail the run
            log.warning("leads chain-purge skipped: %s", exc)
    out = {"tiles_scanned": scanned, "found": found, "new": new, "target": target,
           "met": new >= target, "region": "+".join(sorted(hit)) or region, "cursor": i}
    # Audit the ingest run on the deck (sync_log) — every ingest auditable, real-or-nothing.
    # Defensive getattr keeps test fakes / minimal ledgers working (same pattern as outreach).
    try:
        getattr(ledger, "record_sync", lambda **k: None)(
            source="osm_leads", kind="ingest", rows_in=new, cursor=str(i),
            status="ok" if out["met"] else "partial",
            detail=f"{scanned} tiles scanned, {found} found, {new} new")
    except Exception as exc:  # noqa: BLE001 — audit is observability, never the ingest
        log.debug("leads sync_log record skipped: %s", exc)
    log.info("leads cron (self-replenishing frontier): %s", out)
    from utah import alerts
    alerts.leads_probate(out, kind="leads")   # daily pipeline push (never raises)
    return out


def _maps_kind(types: list[str]) -> str:
    for t in types:
        if t in ("general_contractor", "plumber", "electrician", "roofing_contractor",
                 "hvac_contractor", "landscaper", "home_goods_store"):
            return t
    return "trade"


def parse_maps_place(place: dict) -> dict | None:
    """Maps place → lead dict if it has a phone + is a pitchable SMB; else None.

    Niche-broadening (Michael, 2026-06-09): a place WITH a website is kept (was
    discarded) — the site is stored in contact for precise email enrichment."""
    name = (place.get("name") or "").strip()
    if not name or is_national_chain(name):
        return None
    phone = normalize_phone(place.get("phone") or "")
    if not phone:
        return None
    kind = _maps_kind(place.get("types") or [])
    if not _is_pitchable_smb(name, kind):
        return None
    contact: dict[str, str] = {"phone": phone}
    website = (place.get("website") or "").strip()
    if website:
        contact["website"] = website
    if place.get("address"):
        contact["address"] = place["address"]
    return Lead(name=name, kind=kind, contact=contact, source="google_maps").as_dict()


def find_maps_no_website_trades(text_query: str, lat: float, lng: float, *,
                                radius_m: int = MAPS_SCOUT_RADIUS_M,
                                max_results: int = MAPS_SCOUT_MAX_RESULTS,
                                search_fn=None) -> list[dict]:
    """Google Maps Text Search for *text_query* near (lat,lng); keep no-website + phone."""
    from utah.integrations import maps

    res = maps.text_search(
        f"{text_query} near {lat},{lng}", lat=lat, lng=lng,
        radius_m=radius_m, max_results=max_results, fetch=search_fn,
    )
    if not res.get("available"):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for place in res.get("places") or []:
        lead = parse_maps_place(place)
        if not lead:
            continue
        key = lead["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(lead)
    return out


def _load_maps_cursor() -> int:
    try:
        return int(json.loads(MAPS_SCOUT_STATE.read_text()).get("i", 0))
    except (OSError, ValueError, TypeError) as exc:  # absent/corrupt → start at 0
        log.debug("maps scout cursor reset to 0 (%s)", exc)
        return 0


def _save_maps_cursor(i: int) -> None:
    try:
        MAPS_SCOUT_STATE.parent.mkdir(parents=True, exist_ok=True)
        MAPS_SCOUT_STATE.write_text(json.dumps({"i": i}))
    except Exception as exc:  # noqa: BLE001
        log.warning("maps scout cursor write failed: %s", exc)


def scout_maps_trades(ledger, text_query: str, center_name: str, lat: float, lng: float,
                      *, search_fn=None) -> dict:
    """Maps trade scout for one query × center → ledger (source=google_maps)."""
    found = find_maps_no_website_trades(text_query, lat, lng, search_fn=search_fn)
    new = enriched = 0
    region = f"Maps {center_name} [{text_query}]"
    for s in found:
        contact = s.get("contact") or {}
        phone = contact.get("phone") or ""
        if phone and ledger.has_phone_lead(phone, "google_maps"):
            continue
        if ledger.record_lead(s["name"], s["kind"], region, "google_maps", contact=contact):
            new += 1
        elif ledger.update_lead(s["name"], region, contact=contact):
            enriched += 1
    log.info("maps scout %s/%s: found=%d new=%d enriched=%d", text_query, center_name,
             len(found), new, enriched)
    return {"found": len(found), "new": new, "enriched": enriched, "region": region,
            "query": text_query}


def run_maps_scheduled(*, ledger=None, foundation_gate=None, target: int = MAPS_DAILY_TARGET,
                       search_fn=None) -> dict:
    """``com.utah.leads-maps`` cron — rotate trade query × metro, ingest phone leads."""
    from utah import foundation

    gate = foundation.gate_cron if foundation_gate is None else foundation_gate
    skip = gate("leads_maps")
    if skip:
        return skip
    if ledger is None:
        from utah.product.ledger import Ledger
        ledger = Ledger()
    combos = [(q, c) for q in MAPS_TRADE_QUERIES for c in MAPS_SCOUT_CENTERS]
    if not combos:
        return {"found": 0, "new": 0, "target": target, "met": False}
    start = _load_maps_cursor() % len(combos)
    i = start
    found = new = enriched = scanned = 0
    hit: set[str] = set()
    while new < target and scanned < len(combos):
        query, (cname, lat, lng) = combos[i % len(combos)]
        try:
            res = scout_maps_trades(ledger, query, cname, lat, lng, search_fn=search_fn)
            found += res["found"]
            new += res["new"]
            enriched += res.get("enriched", 0)
            hit.add(cname)
        except Exception as exc:  # noqa: BLE001
            log.warning("maps scout %s/%s failed: %s", query, cname, exc)
        scanned += 1
        i = (i + 1) % len(combos)
        if new >= target:
            break
    _save_maps_cursor(i)
    out = {"found": found, "new": new, "enriched": enriched, "target": target,
           "met": new >= target, "region": "+".join(sorted(hit)) or "maps",
           "cursor": i, "source": "google_maps"}
    try:
        getattr(ledger, "record_sync", lambda **k: None)(
            source="google_maps", kind="ingest", rows_in=new, cursor=str(i),
            status="ok" if out["met"] else "partial",
            detail=f"{scanned} queries, {found} found, {new} new phone leads")
    except Exception as exc:  # noqa: BLE001
        log.debug("maps sync_log skipped: %s", exc)
    log.info("leads maps cron: %s", out)
    return out


def run_maps_bulk(*, target: int = MAPS_BULK_TARGET, queries: list[str] | None = None,
                  ledger=None, foundation_gate=None, search_fn=None,
                  sleep_s: float = 0.15) -> dict:
    """Sweep all query × metro combos until *target* distinct Maps phones land (or exhausted).
    Handyman pipeline bulk fill — run manually or via ``UTAH_MAPS_BULK_TARGET=3000``."""
    import time

    from utah import foundation

    gate = foundation.gate_cron if foundation_gate is None else foundation_gate
    skip = gate("leads_maps")
    if skip:
        return skip
    if ledger is None:
        from utah.product.ledger import Ledger
        ledger = Ledger()
    qlist = queries or MAPS_HANDYMAN_QUERIES
    combos = [(q, c) for q in qlist for c in MAPS_SCOUT_CENTERS]
    start_phones = ledger.count_maps_phone_leads()
    found = new = enriched = scanned = 0
    hit: set[str] = set()
    for query, (cname, lat, lng) in combos:
        if ledger.count_maps_phone_leads() - start_phones >= target:
            break
        try:
            res = scout_maps_trades(ledger, query, cname, lat, lng, search_fn=search_fn)
            found += res["found"]
            new += res["new"]
            enriched += res.get("enriched", 0)
            hit.add(cname)
        except Exception as exc:  # noqa: BLE001
            log.warning("maps bulk %s/%s failed: %s", query, cname, exc)
        scanned += 1
        if sleep_s and search_fn is None:
            time.sleep(sleep_s)
    total_phones = ledger.count_maps_phone_leads()
    gained = total_phones - start_phones
    out = {"found": found, "new": new, "enriched": enriched, "target": target,
           "met": gained >= target, "phones_total": total_phones, "phones_gained": gained,
           "scanned": scanned, "combos": len(combos), "region": "+".join(sorted(hit)) or "maps",
           "source": "google_maps", "queries": qlist}
    log.info("leads maps BULK: %s", out)
    return out


__all__ = ["is_national_chain", "build_query", "find_no_website_smbs", "scout",
           "scout_frontier", "frontier_tiles", "run_scheduled", "_extract_contact",
           "OVERPASS_MAX_RETRIES", "OVERPASS_BACKOFF_BASE_S", "OVERPASS_TILE_PAUSE_S",
           "OVERPASS_MIRROR_SWEEPS",
           "normalize_phone", "parse_maps_place", "find_maps_no_website_trades",
           "scout_maps_trades", "run_maps_scheduled", "run_maps_bulk",
           "COWETA_BBOX", "METRO_BBOX", "FRONTIER_BBOX", "TILE_STEP", "DAILY_TARGET",
           "MAX_TILES_PER_RUN", "FRONTIER_STATE", "MAPS_SCOUT_STATE", "NATIONAL_CHAINS",
           "MAPS_TRADE_QUERIES", "MAPS_HANDYMAN_QUERIES", "MAPS_SCOUT_CENTERS",
           "MAPS_DAILY_TARGET", "MAPS_BULK_TARGET"]
