# Leads — "Black Label Leads" (outreach)  ·  branch-off app

**Dashboard /goal label:** `Black Label Leads`  ·  **app id:** `black-label-leads`  ·  logo: one per app id


> COPY for branch-off. Source of truth = `~/ProjectUtah/utah/product/` + `utah/`. Editing here does not touch the live system.
>
> **Vision: this is *our* Apollo.io — we own it, no per-seat SaaS.** Own the search/database
> (find anyone in any market, anywhere in the US) + own the sequencer (send from the client's
> own inbox, autonomously).

## Spec (what the app must do)
- Client picks **any market they request** — the finder pulls real people/businesses in that market.
- Client **inputs their own email**; the app uses **their email, autonomously**, to send the outreach and follow-ups.
- Deliverability-safe: warmup caps, MX gate, reply/bounce detection, bounce auto-pause.

## Modules (copied)
leads, leads_eval, leads_status, enrich, outreach, pipeline, mail_status

## Status
- ✅ **any market the client requests** — `leads.market_selectors/build_market_query/find_market_smbs/scout_market` (known verticals → OSM selectors; unknown → name-keyword fallback).
- ✅ **entire United States** — `leads.scout_market_in(market, location)` (geocode any US location) + `leads.scout_market_us(market)` (40-metro coast-to-coast sweep).
- ✅ **client's own email, autonomous send** — `mail.register_client_account` / `send_as` / `send_as_client` (replies route to the client).
- ✅ exists: enrichment (`enrich`), deliverability-gated rotation (`outreach`), reply/bounce handling, bounce auto-pause.
- 🔨 to fully own Apollo: **people-level contacts** (decision-maker names/titles/emails, not just business listings) + **email verification at scale** + a persistent contact DB — Apollo's real moat.

## Shared spine (`../_shared_core/`)
config, failures, db_pool, foundation, alerts, mail, mail_capacity, mail_replies, sms, objects
## Live crons (utah/ops/launchd/)
com.utah.leads · com.utah.leads-maps · com.utah.enrich · com.utah.outreach
