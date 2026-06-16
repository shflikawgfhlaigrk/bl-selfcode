# Leads — "Black Label Leads" (outreach)  ·  branch-off app

> COPY for branch-off. Source of truth = `~/ProjectUtah/utah/product/` + `utah/`. Editing here does not touch the live system.

## Spec (what the app must do)
- Client picks **any market they request** — the finder pulls real people/businesses in that market.
- Client **inputs their own email**; the app uses **their email, autonomously**, to send the outreach and follow-ups.
- Deliverability-safe: warmup caps, MX gate, reply/bounce detection, bounce auto-pause.

## Modules (copied)
leads, leads_eval, leads_status, enrich, outreach, pipeline, mail_status

## Status
- ✅ exists: finder (`leads`), enrichment (`enrich`), deliverability-gated send + rotation (`outreach`, uses `_shared_core/mail*`), reply/bounce handling, bounce auto-pause.
- 🔨 to build: client-configurable target market (arbitrary vertical on request); **client's own email as the sending identity** (input → authenticate → autonomous send from it); per-client isolation.

## Shared spine (`../_shared_core/`)
config, failures, db_pool, foundation, alerts, mail, mail_capacity, mail_replies, sms, objects
## Live crons (utah/ops/launchd/)
com.utah.leads · com.utah.leads-maps · com.utah.enrich · com.utah.outreach
