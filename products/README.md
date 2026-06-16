# Utah Products — branch-off areas (COPIES)

Each product split into its own area so it can be branched off into its own app.

> **Ace / ProjectUtah is still the one.** Everything here is a **copy**. The live source of
> truth stays in `~/ProjectUtah/utah/product/` and `~/ProjectUtah/utah/` (core). Editing files
> in `products/` does **not** touch the running daemon, the 13 launchd services, or the 286
> imports across the codebase. Nothing live was moved or renamed.

## The areas

| Area | Product | Modules | Live cron services |
|------|---------|--------:|--------------------|
| `trading/`    | Signal/edge engines (OOS edge-gating, fire grading) | 10 | engine-audit, grade-fires, signals |
| `leads/`      | Cold outreach (finder → enrich → gated send)         | 7  | leads, leads-maps, enrich, outreach |
| `probate/`    | Probate capture → enrich → heir outreach            | 4  | probate, probate-enrich, probate-outreach |
| `realestate/` | Route optimization (TSP/VRP canvasser + fleet)      | 1  | — (on-demand /api/route) |
| `marketing/`  | Sitegen, reels, spotlight outreach                  | 4  | marketer |
| `revenue/`    | Sales ledger + Stripe→Utah mirror                   | 2  | stripe-sync |
| `_shared_core/` | The spine every product imports (copied once)     | 11 | — |

Each area has a `MANIFEST.md`: its modules, the shared-spine modules it imports, its launchd
services, and how to run it.

## Standing one up as its own app

1. Take the area folder (e.g. `leads/`).
2. Add the `_shared_core/` modules its `MANIFEST.md` lists.
3. Vendor the `utah/integrations/` and `utah/daemon/` packages it needs from Utah core.
4. Re-point imports from `utah.product.<mod>` / `utah.<core>` to the app's own package root.

## Not split out (shared/dashboard, not standalone products)

`brief, clock, console, jobs_status, tasks, timers, trackers, selfcode_web, weather` — these are
cross-cutting dashboard/assistant modules, not branch-off products. Left in `utah/product/`.

_Generated as copies; `utah/` is untouched. Regenerate by re-running the copy step — never edit
live code from here._
