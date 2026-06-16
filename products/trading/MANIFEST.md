# Trading  ·  branch-off app

**Dashboard /goal label:** `Black Label Trading`  ·  **app id:** `black-label-trading`  ·  logo: one per app id


> COPY for branch-off. Source of truth = `~/ProjectUtah/utah/product/` + `utah/`.

## Spec (what the app must do)
- Take **any WealthCharts login** and **any prop firm** the client uses.
- Run the trading engines and **just show the signals** (signal-only).

## Modules (copied)
trading, signals, backtest, engine_audit, engine_status, fire_grader, research_signal, researcher, trade_alert, trade_lore, prop_accounts

## Status
- ✅ **connection intake** — `prop_accounts.register_connection(client_id, wealthcharts=…, prop_firm=…)`: any WealthCharts login + **any** prop firm (no allow-list gate; `KNOWN_PROP_FIRMS` is a hint only). Atomic store, per-client.
- ✅ exists: the engine fleet + OOS edge-gate + fire grading; WealthCharts feed integration; signal-only output.
- 🔨 next (touches the live feed — held for review): drive each client's signal view from their stored connection.

## Shared spine (`../_shared_core/`)
config, failures, db_pool, alerts, mail
## Live crons
com.utah.signals (python -m utah.product.signals) · com.utah.engine-audit · com.utah.grade-fires
