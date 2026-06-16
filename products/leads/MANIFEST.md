# Leads / Cold Outreach — branch-off manifest

> **COPY for branch-off.** Source of truth stays in `~/ProjectUtah/utah/product/` (and `utah/` core).
> These files are copies; edits here do **not** affect the live Ace/Utah system.

Finder → enrich → deliverability-gated send, with reply/bounce-aware rotation.

## Modules (copied from `utah/product/`)
leads, leads_eval, leads_status, enrich, outreach, pipeline, mail_status

## Shared spine this product imports (copied in `../_shared_core/`)
config, failures, db_pool, foundation, alerts, mail, mail_capacity, mail_replies, sms, objects

> It also pulls the `utah.integrations` and `utah.daemon` packages from Utah core — vendor those from `~/ProjectUtah/utah/` when standing up the app.

## Live launchd services (in `~/ProjectUtah/ops/launchd/`)
com.utah.leads · com.utah.leads-maps · com.utah.enrich → enrich.run_scheduled() · com.utah.outreach → outreach.run_scheduled()

## Entry points / run
`enrich.run_scheduled(limit=N)` · `outreach.run_scheduled()` (hourly cron)
