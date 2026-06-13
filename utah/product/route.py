"""Route-optimization engine — one brain, two configs, two products.

Give it a pile of addresses + a start point; it returns the shortest order to drive them,
the total miles, the est. drive time, and a Google Maps link. The math nobody wants to do
by hand (Traveling Salesman / Vehicle Routing), in seconds, on free libraries.

**Free by construction** — geocoding defaults to OpenStreetMap **Nominatim** (no key, 1
req/sec, cached forever at ``~/.utah/cache/geocode.json``); the solver runs on the already
installed ``numpy`` (no ``ortools``, no paid service). The existing Google key
(``utah.integrations.maps.geocode``) is an optional upgrade, not a requirement.

**Honest by construction** — a geocode MISS flags the stop ``unroutable``; a coordinate is
never invented (same contract as ``maps.geocode``). The geocoder is *injectable* so all of
the routing logic is unit-tested offline with zero network.

Two configs ride the same engine:

* ``CANVASSER`` — one person, prioritises the highest-value stops first (door-to-door /
  RE-investor product; also dogfoods our own probate + leads).
* ``FLEET`` / ``FLEET_2`` — many vehicles from a depot, work split across them (delivery /
  field-service product — the margin play).
"""
from __future__ import annotations

import json
import logging
import math
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from utah.daemon import runtime

log = logging.getLogger("utah.product.route")

#: Mean Earth radius in statute miles (great-circle distance).
EARTH_RADIUS_MI = 3958.7613
#: Default city/suburban driving speed for the drive-time estimate.
AVG_SPEED_MPH = 30.0
#: How hard a stop's priority pulls it earlier in the visit order (Canvasser).
PRIORITY_WEIGHT = 1.0

GEOCODE_CACHE = runtime.UTAH_HOME / "cache" / "geocode.json"
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "Utah/1.0 route-optimizer (educational)"
#: Nominatim asks for <=1 request/second; we honour it between live lookups.
_NOMINATIM_MIN_INTERVAL_S = 1.05

Coord = tuple[float, float]
#: A geocoder maps an address string -> (lat, lng) or None on a miss. Injectable.
Geocoder = Callable[[str], Optional[Coord]]


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
@dataclass
class Stop:
    """One place to visit. ``priority`` (higher = more valuable) drives Canvasser order;
    ``demand`` reserves capacity for Fleet. ``lat``/``lng`` are filled by geocoding."""
    label: str
    address: str = ""
    lat: float | None = None
    lng: float | None = None
    priority: float = 0.0
    demand: float = 0.0
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    """A routing "personality"."""
    name: str
    vehicles: int = 1
    capacity: float | None = None      # per-vehicle demand ceiling (Fleet)
    use_priority: bool = False         # value-first ordering (Canvasser)
    round_trip: bool = True            # return to the depot/start
    max_stops: int | None = None       # cap a single route to a day's work (Canvasser)
    avg_speed_mph: float = AVG_SPEED_MPH


#: The two ship-able configs (plus a 2-vehicle Fleet for small crews / tests).
CANVASSER = Config(name="canvasser", vehicles=1, use_priority=True, round_trip=False)
FLEET = Config(name="fleet", vehicles=3, round_trip=True)
FLEET_2 = Config(name="fleet", vehicles=2, round_trip=True)


@dataclass
class VehicleRoute:
    """One driver's ordered run. ``stops`` are indices into the original stops list."""
    stops: list[int]
    total_miles: float
    minutes: float
    maps_url: str


@dataclass
class RouteResult:
    routes: list[VehicleRoute]
    unroutable: list[int]
    total_miles: float
    total_minutes: float

    @property
    def stop_count(self) -> int:
        return sum(len(r.stops) for r in self.routes)


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #
def haversine_miles(a: Coord, b: Coord) -> float:
    """Great-circle distance between two ``(lat, lng)`` points, in statute miles."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(min(1.0, math.sqrt(h)))


def distance_matrix(points: Sequence[Coord]) -> list[list[float]]:
    """N points -> symmetric N×N miles matrix (zero diagonal)."""
    n = len(points)
    m = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = haversine_miles(points[i], points[j])
            m[i][j] = m[j][i] = d
    return m


def path_miles(matrix: list[list[float]], order: Sequence[int], *, round_trip: bool) -> float:
    """Total miles travelled following ``order`` (optionally returning to ``order[0]``)."""
    if len(order) < 2:
        return 0.0
    total = sum(matrix[order[k]][order[k + 1]] for k in range(len(order) - 1))
    if round_trip:
        total += matrix[order[-1]][order[0]]
    return total


# --------------------------------------------------------------------------- #
# Solver  (nearest-neighbour seed + 2-opt polish; priority biases the seed)
# --------------------------------------------------------------------------- #
def solve_order(
    matrix: list[list[float]],
    *,
    start: int = 0,
    priorities: Sequence[float] | None = None,
    round_trip: bool = False,
) -> list[int]:
    """Return the visit order (starting at ``start``) that keeps total distance low.

    With ``priorities`` (higher = more valuable), the greedy seed treats a valuable stop as
    if it were closer, so high-value stops land early — and 2-opt is skipped so distance
    polishing can't scramble that value ordering. Without priorities, 2-opt runs.
    """
    n = len(matrix)
    if n <= 1:
        return list(range(n))
    unvisited = set(range(n)) - {start}
    order = [start]
    cur = start
    while unvisited:
        if priorities is None:
            nxt = min(unvisited, key=lambda j: (matrix[cur][j], j))
        else:
            nxt = min(
                unvisited,
                key=lambda j: (matrix[cur][j] / (1.0 + PRIORITY_WEIGHT * max(0.0, priorities[j])), j),
            )
        order.append(nxt)
        unvisited.discard(nxt)
        cur = nxt
    if priorities is None:
        order = _two_opt(matrix, order, round_trip=round_trip)
    return order


def _two_opt(matrix: list[list[float]], order: list[int], *, round_trip: bool) -> list[int]:
    """Repeatedly reverse a sub-segment when it shortens the path; ``order[0]`` stays fixed."""
    best = order[:]
    best_len = path_miles(matrix, best, round_trip=round_trip)
    improved = True
    while improved:
        improved = False
        for i in range(1, len(best) - 1):
            for k in range(i + 1, len(best)):
                cand = best[:i] + best[i : k + 1][::-1] + best[k + 1 :]
                cand_len = path_miles(matrix, cand, round_trip=round_trip)
                if cand_len + 1e-9 < best_len:
                    best, best_len, improved = cand, cand_len, True
    return best


# --------------------------------------------------------------------------- #
# Geocoding  (free OSM Nominatim by default, file-cached; injectable)
# --------------------------------------------------------------------------- #
def _load_cache() -> dict:
    try:
        return json.loads(GEOCODE_CACHE.read_text())
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        GEOCODE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        GEOCODE_CACHE.write_text(json.dumps(cache))
    except OSError as exc:  # pragma: no cover - disk issue, non-fatal
        log.warning("geocode cache write failed: %s", exc)


def geocode_osm(address: str) -> Optional[Coord]:
    """Free OSM Nominatim geocode -> ``(lat, lng)`` or ``None``. Cached forever; honours the
    1 req/sec courtesy limit; never raises and never fabricates a coordinate."""
    address = (address or "").strip()
    if not address:
        return None
    cache = _load_cache()
    if address in cache:
        hit = cache[address]
        return (hit[0], hit[1]) if hit else None
    last = getattr(geocode_osm, "_last_call", 0.0)
    wait = _NOMINATIM_MIN_INTERVAL_S - (time.time() - last)
    if wait > 0:
        time.sleep(wait)
    geocode_osm._last_call = time.time()  # type: ignore[attr-defined]
    try:
        url = _NOMINATIM_URL + "?" + urllib.parse.urlencode(
            {"q": address, "format": "json", "limit": 1}
        )
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        coord = (float(data[0]["lat"]), float(data[0]["lon"])) if data else None
    except Exception as exc:  # noqa: BLE001 - network/parse: honest miss, never crash
        log.warning("geocode failed for %r: %s", address[:50], exc)
        return None
    cache[address] = list(coord) if coord else None
    _save_cache(cache)
    return coord


def geocode_stops(stops: Sequence[Stop], geocoder: Geocoder) -> tuple[list[int], list[int]]:
    """Fill ``lat``/``lng`` on each stop via ``geocoder``. Returns ``(routable, unroutable)``
    index lists. Mutates the stops in place; a miss leaves coords ``None`` (never invented)."""
    routable, unroutable = [], []
    for i, s in enumerate(stops):
        if s.lat is None and s.address:
            hit = geocoder(s.address)
            if hit:
                s.lat, s.lng = float(hit[0]), float(hit[1])
        (routable if s.lat is not None else unroutable).append(i)
    return routable, unroutable


# --------------------------------------------------------------------------- #
# Clustering  (Fleet: split stops across vehicles by angular sweep — deterministic)
# --------------------------------------------------------------------------- #
def _cluster(indices: list[int], stops: Sequence[Stop], k: int) -> list[list[int]]:
    if k <= 1 or len(indices) <= 1:
        return [indices]
    if len(indices) <= k:
        return [[i] for i in indices]
    clat = sum(stops[i].lat for i in indices) / len(indices)
    clng = sum(stops[i].lng for i in indices) / len(indices)
    ordered = sorted(indices, key=lambda i: math.atan2(stops[i].lat - clat, stops[i].lng - clng))
    groups: list[list[int]] = [[] for _ in range(k)]
    base, extra = divmod(len(ordered), k)
    pos = 0
    for g in range(k):
        size = base + (1 if g < extra else 0)
        groups[g] = ordered[pos : pos + size]
        pos += size
    return [g for g in groups if g]


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def maps_url(coords: Sequence[Coord]) -> str:
    """A Google Maps directions link through the ordered coordinates."""
    return "https://www.google.com/maps/dir/" + "/".join(f"{lat:.6f},{lng:.6f}" for lat, lng in coords)


# --------------------------------------------------------------------------- #
# DB adapters — turn real rows into Stops (probate already carries lat/lng/arv)
# --------------------------------------------------------------------------- #
def _dsn(dsn: str | None = None) -> str:
    from utah import config
    return dsn or config.DB_DSN


def _probate_rows_to_stops(rows) -> list[Stop]:
    """Map ``(id, case_name, county, arv, lat, lng)`` rows -> Stops. Pure (no DB), so it is
    unit-tested offline. ARV becomes the priority (visit the biggest deals first)."""
    out: list[Stop] = []
    for rid, case_name, county, arv, lat, lng in rows:
        if lat is None or lng is None:
            continue
        out.append(Stop(
            label=case_name or f"probate#{rid}",
            lat=float(lat), lng=float(lng),
            priority=float(arv or 0.0),
            meta={"id": rid, "county": county, "arv": arv, "kind": "probate"},
        ))
    return out


def stops_from_probate(*, limit: int | None = None, county: str | None = None,
                       dsn: str | None = None) -> list[Stop]:
    """Pull real probate properties (with their enriched lat/lng + ARV) as Stops."""
    import psycopg
    q = ("select id, case_name, county, arv, (heir_contact->>'lat')::float, "
         "(heir_contact->>'lng')::float from probate "
         "where heir_contact->>'lat' is not null")
    params: list = []
    if county:
        q += " and lower(county) = lower(%s)"
        params.append(county)
    q += " order by arv desc nulls last"
    if limit:
        q += " limit %s"
        params.append(limit)
    with psycopg.connect(_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute(q, params)
        rows = cur.fetchall()
    return _probate_rows_to_stops(rows)


def _lead_rows_to_stops(rows) -> list[Stop]:
    """Map ``(id, name, region, address)`` lead rows -> Stops (need geocoding; no value field)."""
    out: list[Stop] = []
    for rid, name, region, address in rows:
        if not address:
            continue
        out.append(Stop(label=name or f"lead#{rid}", address=address,
                        meta={"id": rid, "region": region, "kind": "lead"}))
    return out


def stops_from_leads(*, limit: int | None = None, region: str | None = None,
                     dsn: str | None = None) -> list[Stop]:
    """Pull real SMB leads (address from ``contact->>'address'``) as Stops to be geocoded."""
    import psycopg
    q = "select id, name, region, contact->>'address' from leads where contact->>'address' is not null"
    params: list = []
    if region:
        q += " and region = %s"
        params.append(region)
    if limit:
        q += " limit %s"
        params.append(limit)
    with psycopg.connect(_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute(q, params)
        rows = cur.fetchall()
    return _lead_rows_to_stops(rows)


def optimize(
    stops: Sequence[Stop],
    config: Config,
    *,
    depot: Stop | None = None,
    geocoder: Geocoder | None = None,
) -> RouteResult:
    """Geocode -> (split) -> solve -> assemble. The one call both products use."""
    geocoder = geocoder or geocode_osm
    routable, unroutable = geocode_stops(stops, geocoder)
    if depot is not None and depot.lat is None and depot.address:
        hit = geocoder(depot.address)
        if hit:
            depot.lat, depot.lng = float(hit[0]), float(hit[1])
    if not routable:
        return RouteResult(routes=[], unroutable=unroutable, total_miles=0.0, total_minutes=0.0)

    groups = _cluster(routable, stops, config.vehicles) if config.vehicles > 1 else [routable]
    routes = [vr for g in groups if (vr := _solve_group(g, stops, config, depot)) and vr.stops]
    return RouteResult(
        routes=routes,
        unroutable=unroutable,
        total_miles=sum(r.total_miles for r in routes),
        total_minutes=sum(r.minutes for r in routes),
    )


def _solve_group(
    group: list[int], stops: Sequence[Stop], config: Config, depot: Stop | None
) -> VehicleRoute | None:
    has_depot = depot is not None and depot.lat is not None
    pts: list[Coord] = []
    origin: list[int | None] = []            # internal idx -> original stop idx (None = depot)
    if has_depot:
        pts.append((depot.lat, depot.lng))   # type: ignore[arg-type]
        origin.append(None)
    for i in group:
        pts.append((stops[i].lat, stops[i].lng))  # type: ignore[arg-type]
        origin.append(i)
    if len(pts) < 2:
        if not group:
            return None
        i = group[0]
        return VehicleRoute(stops=[i], total_miles=0.0, minutes=0.0,
                            maps_url=maps_url([(stops[i].lat, stops[i].lng)]))

    matrix = distance_matrix(pts)
    priorities = None
    if config.use_priority:
        priorities = [0.0 if o is None else max(0.0, stops[o].priority) for o in origin]

    order = solve_order(matrix, start=0, priorities=priorities, round_trip=config.round_trip)

    if config.max_stops is not None:
        keep = config.max_stops + (1 if has_depot else 0)
        order = order[:keep]

    miles = path_miles(matrix, order, round_trip=config.round_trip)
    ordered_coords = [pts[k] for k in order]
    if config.round_trip and len(order) > 1:
        ordered_coords.append(pts[order[0]])
    stop_indices = [origin[k] for k in order if origin[k] is not None]
    return VehicleRoute(
        stops=stop_indices,
        total_miles=miles,
        minutes=miles / config.avg_speed_mph * 60.0,
        maps_url=maps_url(ordered_coords),
    )
