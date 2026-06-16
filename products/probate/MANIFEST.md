# Probate — branch-off manifest

> **COPY for branch-off.** Source of truth stays in `~/ProjectUtah/utah/product/` (and `utah/` core).
> These files are copies; edits here do **not** affect the live Ace/Utah system.

Statewide probate capture, property/ARV enrichment, heir-contact outreach.

## Modules (copied from `utah/product/`)
probate, probate_export, probate_outreach, property

## Shared spine this product imports (copied in `../_shared_core/`)
config, failures, alerts

> It also pulls the `utah.integrations` and `utah.daemon` packages from Utah core — vendor those from `~/ProjectUtah/utah/` when standing up the app.

## Live launchd services (in `~/ProjectUtah/ops/launchd/`)
com.utah.probate → probate.run_scheduled() · com.utah.probate-enrich → property.enrich_ledger() · com.utah.probate-outreach → probate_outreach.run_scheduled()

## Entry points / run
`probate.run_scheduled()` (daily capture) · `property.enrich_ledger(limit=N)`
