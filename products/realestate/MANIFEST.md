# Real Estate  ·  branch-off app

**Dashboard /goal label:** `Black Label Real Estate`  ·  **app id:** `black-label-real-estate`  ·  logo: one per app id


> COPY for branch-off. Source of truth = `~/ProjectUtah/utah/product/` + `utah/`.

## Spec (what the app must do)
- **Probate** lead pipeline (statewide capture → heir contact).
- **Find builders** in the target areas.
- The **3-mile radius** enrichment (comps / ownership / debt) — all of these must run together.

## Modules (copied)
probate, probate_export, probate_outreach, property, route, builders

## Status
- ✅ **builder-finder** — `builders.find_builders(bbox)` / `find_builders_near(lat,lon)` / `scout_builders(location)`: builders/contractors/roofers in an area or within ~3 miles of a property (`THREE_MILES_KM`), reusing the proven leads any-market finder.
- ✅ exists: probate capture + outreach (`probate`, `probate_outreach`), property/ARV + **3-mile radius** enrich (`property`), canvassing route optimization (`route`).
- 🔨 next: one schedule that runs probate + 3-mile + builder-finder + ownership/debt enrich together.

## Shared spine (`../_shared_core/`)
config, failures, alerts, db_pool, foundation
## Live crons
com.utah.probate · com.utah.probate-enrich (property.enrich_ledger) · com.utah.probate-outreach
