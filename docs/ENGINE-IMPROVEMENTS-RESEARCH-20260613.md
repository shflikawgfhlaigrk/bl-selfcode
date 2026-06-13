# Engine Improvement Research — 2026-06-13

Researched ways to improve the trading fleet, grounded in what the nightly audit + the
real `bars` table actually show (not generic advice). Every recommendation maps to a
concrete file/change and is ordered by leverage × effort. No paid services (binding).

## Grounding — what the data says today

From the live audit (`~/.utah/audit/latest.md`) and the `bars` table:

- **4 of 33 (engine,symbol) pairs prove OOS edge**: meanrev/CM.NQM6 (88%, +491pt),
  research/QQQ (+0.015R), research/SPY (+0.056R), research/GLD (+0.006R).
- **Data depth is tiny and the binding constraint**: 0.1–2.4 days of history per symbol
  (MNQ 1.4d, NQ 0.6d, ES 0.1d). Irregular cadence: 15s futures vs ~73–114s ETFs with gaps.
- **research edges are marginal** (~40% win at 1.5R = barely above the 40% breakeven) and
  it **loses on index futures** (NQ −0.26R) — complementary to meanrev, not redundant.
- **meanrev "close but no cigar"**: SPY 80.5%/+1.12net, DIA 76.7%, IWM 78% — all real
  but suppressed by the 87% win-floor.
- **33 combos tested on ~2 days of data** → textbook multiple-testing / selection-bias
  risk: the more (engine,symbol) pairs we test, the more likely a *false* edge clears the
  gate. The current gate has no correction for this.

## Prioritized improvements

### P0 · Deflated Sharpe Ratio gate — selection-bias correction  ⟵ recommended next build
**Why:** With 33 trials on thin data, "edge_proven" is vulnerable to luck-of-the-draw.
The **Deflated Sharpe Ratio (DSR)** (Bailey & López de Prado) adjusts the significance
bar for *the number of trials*, skewness, kurtosis, and sample length — exactly our
situation. A pair proves edge only if its Sharpe survives deflation by how many combos
we tried.
**Leverage:** HIGH (directly fixes the audit's biggest integrity gap). **Effort:** LOW —
a pure function. **Data:** none needed.
**Change:** add `deflated_sharpe(returns, n_trials, ...)` to `backtest.py` (TDD), feed
`n_trials = len(implemented_engines) × len(backtestable_symbols)` from the audit, and
make `edge_ok` require DSR p-value < 0.05. Unifies breakout/meanrev/research under one
statistically-honest test instead of three ad-hoc floors. Fits Utah's "never paint a
number" ethos perfectly.
Sources: [Deflated Sharpe Ratio (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551),
[Probability of Backtest Overfitting (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253),
[Wikipedia: Deflated Sharpe ratio](https://en.wikipedia.org/wiki/Deflated_Sharpe_ratio).

### P0 · Deepen the bar history — the multiplier under everything
**Why:** Every "edge" sits on 0.1–2.4 days. Confidence, parameter robustness, regime
detection, and meta-labeling are all data-starved. This is the #1 force multiplier.
**Effort:** MEDIUM. **Data:** the goal itself.
**Change:** (a) lengthen continuous WC capture (don't truncate the `bars` table); (b)
backfill from a free historical source where licensing allows; (c) the nightly audit
already writes dated reports — add an edge-count *trend line* so we can watch convergence
as history deepens (the 6-month KPI). No paid feeds.

### P1 · Meta-labeling — precision filter for the research engine
**Why:** research has good *recall* (it fires) but low *precision* (~40% win). Meta-
labeling (López de Prado) keeps the composite as the primary "side" model and trains a
**secondary bet/no-bet classifier** on its components (trend/level/momentum/regime +
context) vs realized outcomes → suppresses false positives, lifts win%/F1/Sharpe, and
sizes bets. "Increase F1 by filtering false positives the primary already found."
**Leverage:** HIGH for research. **Effort:** MEDIUM. **Data:** needs P0 depth first.
**Change:** log every signal + outcome (the grader already produces outcomes); train an
in-venv logistic/GBM (sklearn, no paid service) offline; gate live fires on its
probability. Build *after* DSR + more data.
Sources: [Meta-Labeling (Wikipedia)](https://en.wikipedia.org/wiki/Meta-Labeling),
[Does Meta-Labeling Add to Signal Efficacy? (Hudson & Thames)](https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy-triple-barrier-method/).

### P1 · Volatility targeting — better sizing, Sharpe-positive on our winners
**Why:** Research shows vol-targeting lifts Sharpe specifically for **equity/momentum**
(our QQQ/SPY/GLD winners) and is **negligible for commodities/futures** — which exactly
matches our data (research wins ETFs, loses NQ). Scale position size to target a constant
risk budget instead of fixed contracts.
**Leverage:** MEDIUM–HIGH. **Effort:** LOW. **Data:** none extra.
**Change:** size = risk_budget / (stop_distance × tick_value), already half-present in the
fire geometry — make it the standard across engines and target constant daily vol.
Sources: [The Impact of Volatility Targeting (Man Group)](https://www.man.com/insights/the-impact-of-volatility-targeting),
[Volatility Targeting (QuantPedia)](https://quantpedia.com/an-introduction-to-volatility-targeting/).

### P2 · Combinatorial Purged Cross-Validation in the nightly audit
Replace the single chronological in/out split with **CPCV** (López de Prado) — validate
each engine across many purged train/test combinations and report **PBO** (probability of
backtest overfit) per engine in the nightly report. A single lucky split can't pass.
Effort MEDIUM; pure; folds into `engine_audit.py`.

### P2 · Regime detection upgrade
Current regime = variance-ratio (Hurst proxy) + return-entropy gate; it already correctly
stands the trend engine down in mean-reverting regimes. Finer granularity (volatility-
regime HMM, or conditional vol-targeting) is a later refinement, not urgent.

### P2 · Bar-cadence normalization
ETF bars are ~73–114s irregular, futures 15s — so "20-bar lookback" spans different wall-
clock per symbol. Resample to fixed 1-min (apples-to-apples features), or move to
**volume bars** (López de Prado) once a volume/tick feed is captured. This also unlocks
the research engine's currently-gated order-flow voters (CVD/absorption/SMT).

## Recommended sequence
1. **DSR gate** (P0, pure, no data) — fixes the integrity gap now; best immediate build.
2. **Deepen history + audit trend line** (P0) — the multiplier; run in parallel.
3. **Volatility targeting** (P1, low effort) — Sharpe lift on the winners.
4. **Meta-labeling** (P1) — once data is deep enough to train honestly.
5. CPCV + cadence normalization + regime upgrades (P2) — as the history matures.

This is the loop the nightly audit drives: each item makes the audit's verdict more
honest or the engines more profitable, and the dated reports measure whether 6-month
convergence is actually happening.
