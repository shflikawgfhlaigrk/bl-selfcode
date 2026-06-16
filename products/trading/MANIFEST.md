# Trading / Signals — branch-off manifest

> **COPY for branch-off.** Source of truth stays in `~/ProjectUtah/utah/product/` (and `utah/` core).
> These files are copies; edits here do **not** affect the live Ace/Utah system.

Multi-strategy signal engines with OOS edge-gating and honest fire grading.

## Modules (copied from `utah/product/`)
trading, signals, backtest, engine_audit, engine_status, fire_grader, research_signal, researcher, trade_alert, trade_lore

## Shared spine this product imports (copied in `../_shared_core/`)
config, failures, db_pool, alerts, mail

> It also pulls the `utah.integrations` and `utah.daemon` packages from Utah core — vendor those from `~/ProjectUtah/utah/` when standing up the app.

## Live launchd services (in `~/ProjectUtah/ops/launchd/`)
com.utah.engine-audit → engine_audit.run_scheduled() · com.utah.grade-fires → fire_grader · com.utah.signals → python -m utah.product.signals

## Entry points / run
`python -m utah.product.signals` (live signal loop) · `engine_audit.run_scheduled()` (nightly review)
