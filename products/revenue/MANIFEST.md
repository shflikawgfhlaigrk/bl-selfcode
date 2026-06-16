# Revenue / Cash Register — branch-off manifest

> **COPY for branch-off.** Source of truth stays in `~/ProjectUtah/utah/product/` (and `utah/` core).
> These files are copies; edits here do **not** affect the live Ace/Utah system.

Sales ledger + read-only Stripe→Utah mirror (the cash register from PR #15).

## Modules (copied from `utah/product/`)
stripe_sync, ledger

## Shared spine this product imports (copied in `../_shared_core/`)
config, failures, alerts

> It also pulls the `utah.integrations` and `utah.daemon` packages from Utah core — vendor those from `~/ProjectUtah/utah/` when standing up the app.

## Live launchd services (in `~/ProjectUtah/ops/launchd/`)
com.utah.stripe-sync → stripe_sync.run_scheduled()

## Entry points / run
`stripe_sync.run_scheduled()` (pull completed charges) · `ledger.record_sale(...)`
