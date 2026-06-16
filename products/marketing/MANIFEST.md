# Marketing — branch-off manifest

> **COPY for branch-off.** Source of truth stays in `~/ProjectUtah/utah/product/` (and `utah/` core).
> These files are copies; edits here do **not** affect the live Ace/Utah system.

Per-lead website generation, short-form reels, and spotlight outreach.

## Modules (copied from `utah/product/`)
marketer, reel_queue, sitegen, news

## Shared spine this product imports (copied in `../_shared_core/`)
config, failures, foundation, local_brain, mail

> It also pulls the `utah.integrations` and `utah.daemon` packages from Utah core — vendor those from `~/ProjectUtah/utah/` when standing up the app.

## Live launchd services (in `~/ProjectUtah/ops/launchd/`)
com.utah.marketer → marketer.run_scheduled()

## Entry points / run
`marketer.run_scheduled()` · `sitegen.render(lead)` (used by the leads conversion-unlock)
