# Route Optimization — Design (2026-06-13)

**Goal:** one route-optimization engine, two configs, two sellable products. Free to run
(scipy/numpy/networkx + OpenStreetMap geocoding — no paid library, no new subscription).

## In dumb terms

Give the engine a pile of addresses and a starting point; it returns the shortest order to
drive them, the total miles, the total drive time, and a map link. The math nobody wants to
do by hand (Traveling Salesman / Vehicle Routing), done in seconds.

## The engine (one brain, shared)

Four steps, each a small testable unit:

1. **Geocode** — address string → `(lat, lng)`. Default: free OpenStreetMap **Nominatim**
   (1 req/sec, cached forever at `~/.utah/cache/geocode.json`). Optional upgrade: the
   existing `utah.integrations.maps.geocode` (Google key). Geocoder is **injectable** so the
   solver is unit-tested offline. **Never fabricates a coordinate** — a miss flags the stop
   `unroutable`, it is not invented.
2. **Measure** — N points → N×N great-circle (haversine) distance matrix (`numpy`).
3. **Solve** — shortest visiting order: nearest-neighbor seed + 2-opt polish (deterministic).
   Single route = TSP. Multi-vehicle = cluster then TSP each.
4. **Assemble** — ordered stops + total miles + est. drive time + Google Maps link.

## The two configs ("personalities")

| | **Config A — Fleet (VRP)** | **Config B — Canvasser (priority TSP)** |
|---|---|---|
| Vehicles | many (split work evenly) | one |
| Knows | depot start+end, vehicle count, capacity, time windows | priority weight per stop (highest-value first) |
| Sells to | delivery / field service (HVAC, couriers, lawn) — **margin play** | RE investors / door-to-door sales |
| Dogfood | — | runs on our own 230 probate + 7,532 leads |

## The two products (same engine, different config + wrapper)

1. **Fleet** — paste stops, get optimized multi-driver routes; charge per vehicle/month.
2. **Canvasser** — paste a lead list, get the smart driving order weighted by deal value.

## Boundaries (Michael's rules)

- Build **engine + both configs + a live demo on the Utah deck (:8766)** — all Utah.
- **Do not touch** the blacklabelbots storefront; the paywall/sale page stays Michael's.
- **No paid services**: free Nominatim default; scipy/numpy/networkx already installed.

## Build order (prove each before the next)

1. Engine core — geocode+cache, distance matrix, solver. (TDD, pure/offline.)
2. Config B (Canvasser) — prove live on real probate (230) + leads (7,532).
3. Config A (Fleet) — multi-vehicle.
4. Deck demo on :8766 (`/route`).

## Grounding (verified live 2026-06-13)

- `leads` (7,532 rows): `contact` JSONB holds `address` (e.g. "123 Cannon Point Road,
  Milledgeville, 31061"). `probate` (230 rows): `arv` numeric = deal value for priority.
- No precomputed coordinates → geocoding is step 1 (one-time, cached).
- Installed: `networkx 3.6.1`, `scipy 1.17.1`, `numpy 2.4.6`, `requests 2.34.2`. No `ortools`
  (not needed).
