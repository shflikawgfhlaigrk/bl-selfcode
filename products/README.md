# Utah Products — the 5 branch-off areas (COPIES)

Five distinct products, each split into its own area as **copies** so it can be branched off
into its own app. **Ace / ProjectUtah is still the one** — every file here is a copy; `utah/`
core and `utah/product/` are untouched, and all 286 imports + 13 launchd services still point
at the live originals.

## The 5 areas

| Area | Product | Spec (what the app must do) |
|------|---------|------------------------------|
| `leads/` | **Black Label Leads** (outreach) | Find people in **any market the client requests**; client **inputs their own email** and the app sends from it **autonomously**. |
| `realestate/` | **Real Estate** | **Probate** + **find builders** in the areas + the **3-mile radius** enrich — all running. |
| `marketing/` | **Marketing** | Use **Apple's top-of-the-line** imagery/video tools to make marketing videos. |
| `trading/` | **Trading** | Take **any WealthCharts login** + **any prop firm**; run the engines and **just show the signals**. |
| `sovereign/` | **Sovereign** | The full assistant — **weather, you name it** — plus an **area to populate the Claude login**. |

`_shared_core/` holds the spine every app imports (config, mail, db, …) **plus billing**
(stripe_sync, ledger), copied once. Each area has a `MANIFEST.md` with its modules, ✅ what
already exists, 🔨 what's still to build, its shared deps, and its launchd crons.

## Standing one up as its own app
1. Take the area folder. 2. Add the `_shared_core/` modules its MANIFEST lists. 3. Vendor
`utah/integrations/` + `utah/daemon/` from core. 4. Re-point imports to the app's package root.

_Copies only — `utah/` is untouched. Never edit live code from here._
