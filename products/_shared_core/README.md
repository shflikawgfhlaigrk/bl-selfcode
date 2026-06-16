# _shared_core — the spine every app imports (COPIES) + billing

Copied once instead of duplicated into all five areas. Source of truth = `~/ProjectUtah/utah/`.

config, failures, db_pool, foundation, alerts, objects — core spine.
mail, mail_capacity, mail_replies, sms — sending (mail is bounce-pause-aware).
stripe_sync, ledger — **billing** (sales ledger + Stripe mirror); shared, since every app takes payment — not one of the 5 product areas.

Still package-level deps to vendor from `utah/`: `utah/integrations/`, `utah/daemon/`.
