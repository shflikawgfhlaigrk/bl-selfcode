# Real Estate  ·  branch-off app

> COPY for branch-off. Source of truth = `~/ProjectUtah/utah/product/` + `utah/`.

## Spec (what the app must do)
- **Probate** lead pipeline (statewide capture → heir contact).
- **Find builders** in the target areas.
- The **3-mile radius** enrichment (comps / ownership / debt) — all of these must run together.

## Modules (copied)
probate, probate_export, probate_outreach, property, route

## Status
- ✅ exists: probate capture + outreach (`probate`, `probate_outreach`), property/ARV + **3-mile radius** enrich (`property`), canvassing route optimization (`route`).
- 🔨 to build: **builder-finder** for the target areas; wire probate + 3mi + ownership/debt enrich to all run on one schedule.

## Shared spine (`../_shared_core/`)
config, failures, alerts, db_pool, foundation
## Live crons
com.utah.probate · com.utah.probate-enrich (property.enrich_ledger) · com.utah.probate-outreach
