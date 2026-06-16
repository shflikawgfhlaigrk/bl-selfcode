# Real Estate / Route Optimization — branch-off manifest

> **COPY for branch-off.** Source of truth stays in `~/ProjectUtah/utah/product/` (and `utah/` core).
> These files are copies; edits here do **not** affect the live Ace/Utah system.

Free TSP/VRP routing — single-canvasser (ARV-priority) and multi-vehicle fleet.

## Modules (copied from `utah/product/`)
route

## Shared spine this product imports (copied in `../_shared_core/`)
config

> It also pulls the `utah.integrations` and `utah.daemon` packages from Utah core — vendor those from `~/ProjectUtah/utah/` when standing up the app.

## Live launchd services (in `~/ProjectUtah/ops/launchd/`)
(none — on-demand via the deck: POST /api/route)

## Entry points / run
`route.optimize(...)` · deck page :8766/route
