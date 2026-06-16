# Trading  ·  branch-off app

> COPY for branch-off. Source of truth = `~/ProjectUtah/utah/product/` + `utah/`.

## Spec (what the app must do)
- Take **any WealthCharts login** and **any prop firm** the client uses.
- Run the trading engines and **just show the signals** (signal-only).

## Modules (copied)
trading, signals, backtest, engine_audit, engine_status, fire_grader, research_signal, researcher, trade_alert, trade_lore

## Status
- ✅ exists: the engine fleet + OOS edge-gate + fire grading; WealthCharts feed integration; signal-only output.
- 🔨 to build: **generalized login intake** (any WC account) and **any-prop-firm** connector; per-client signal view.

## Shared spine (`../_shared_core/`)
config, failures, db_pool, alerts, mail
## Live crons
com.utah.signals (python -m utah.product.signals) · com.utah.engine-audit · com.utah.grade-fires
