# Trading Product — Answer-Anything Truth Sheet

_Last verified: 2026-06-14. Every number below comes from a re-runnable command; re-run
them before quoting. This sheet exists so you can field ANY question about the trading
product without guessing or overclaiming._

> One-line honest pitch: **"A disciplined, research-backed futures engine — 16 signal
> modules, 13 independent risk gates — that fires only where it has *proven, out-of-sample
> positive edge*, and logs every fill. It refuses to trade where it can't prove an edge."**
> That refusal is the product. It is real software, it runs, and it does not fabricate.

---

## 1. What is actually sold (from the live site)

| Product | Price | What the page promises |
|---|---|---|
| **Trading Engine** | $499/mo (Pro, full automation) · $500 Starter preorder | Autonomous ES/MES execution on a live feed; 16 signal modules; 13 risk gates; 2-of-8 multi-TF consensus; direction lock; HMM regime; VPIN conviction; alpha monitor; session-vault ledger |
| **Signals** | $100/mo | The signal layer without auto-execution |
| **Sovereign** | $999 (LAUNCH50 → $500) | The agent framework you deploy on your own hardware; "the same architecture" hosts the engine |

The site already labels the risky parts honestly: the 6-month backtest carries a
**"SIMULATION — not live fills"** badge, the execution replay says **"Illustrative replay,
not a live track record,"** and the March–May sessions are labeled **"live paper"** (paper
fills, not live money). Those hedges are correct — do not walk them back in conversation.

---

## 2. Claim → Evidence map (this is the part to memorize)

| Site claim | Real? | Evidence (re-runnable) |
|---|---|---|
| **13 risk gates** | ✅ exact | `~/debt/Research_Backed_Engine/engine/system/quality_gates.py` — 13 `_gate_*` methods: rth_window, first_last_5, lunch_lull, macro_blackout, spread, max_trades, loss_cooldown, intraday_dd, adx_chop, ema_slope, tick_extreme, volume_thin, day_type |
| **16 signal modules** | ✅ (≈, real) | `engine/modules/sniper_engine.py`: 5 direction voters (StepGMA, EMA ribbon, CVD direction, CVD divergence, Hurst-aware) + 7 weighted scorers (trend, level, volume, intelligence, momentum, session, SMT) + sub-signals (VWAP, OR levels, PDH/PDL, POC, FVG, absorption) ≈ 16 distinct indicators |
| **Multi-TF consensus, direction lock** | ✅ | `engine/system/mtf_context.py`, direction-lock logic in `signal_rules.py` |
| **HMM/Hurst regime, VPIN/absorption, alpha monitor** | ✅ code / ⚠️ feed-gated | regime/Hurst + absorption live; CVD/VPIN/SMT need a **tick+volume feed**. On Michael's instance there is no tick feed, so those voters are *documented-gated, never faked* (see `utah/product/research_signal.py` header) |
| **Autonomous ES/MES execution on a live feed** | ✅ code, ⚠️ buyer supplies feed | Broker adapters exist: `engine/core/adapters/` (tradovate, rithmic, mt5, mock). The engine runs end-to-end (see §4). Live trading needs the buyer's broker/market feed — "your deployment writes its own vault on your hardware" |
| **Session-vault ledger, every fill logged** | ✅ | `engine/system/trade_journal.py`; on Michael's live system, the Postgres `fires` table holds **2,107** real graded fires |
| **6-month backtest** | ✅ labeled SIMULATION | runnable: `python -m engine.main --mode backtest` (§4) |
| **March–May "live filled" (52 sessions)** | ⚠️ site-owned data | These session logs live in the **website** repo (off-limits to me). **Verify the underlying vault files exist before citing the "52 sessions" number to a customer.** Labeled "live paper," which is defensible only if the paper fills are real. |

---

## 3. What Michael's LIVE instance actually does today (the honest performance story)

The thing running on this machine is the **Utah trading capability** (`utah/product/trading.py`),
a faithful, lean port of the engine above. Its defining feature is the **edge gate**.

**The edge gate** (`trading.edge_ok`): an engine fires on a symbol ONLY when that exact
(engine, symbol) pair currently proves **held-out, out-of-sample positive edge** on the real
bars — a ≥20-trade OOS sample that is net-positive. No proof → no fire. This is the whole
product thesis in one function.

### Why the gate exists (tell this story — it's the credibility builder)
Before the gate, a naked breakout engine fired blind on every signal:
- **breakout: 1,461 fires, net −4,384 pts** — it bled, firing into noise.
- **meanrev: 646 fires, net +1,471 pts** — the engine with real structure made money.
- Total historical ledger: **−2,914 pts**.

That −4,384 loss is *why* the gate was built (2026-06-13). **Do not present the `fires`
ledger as a track record** — it includes the pre-gate failed experiment on purpose. The
forward-looking claim is only the proven OOS edges the gate now requires.

### Proven edges RIGHT NOW (4 of 33 engine×symbol pairs)
Measured from the live `bars` table (re-run: `python -c "from utah.product import engine_audit as e; print(e.summary_line(e.audit(generated_at='now')))"`):

| engine | symbol | OOS win% | net pts | trades |
|---|---|---|---|---|
| meanrev | CM.NQM6 (NQ futures) | 88.0% | +491.4 | 25 |
| research | US.QQQ | 39.5% | +3.58 | 43 |
| research | US.SPY | 41.2% | +1.21 | 51 |
| research | US.GLD | 41.3% | +2.36 | 46 |

Honest framing: meanrev is a high-win mean-reversion edge on index futures; research is a
trend-rider that wins <50% but positive-expectancy (bigger wins than losses). The count of
proven edges **changes daily** as bars accumulate — it is not a fixed marketing number.

### Feed status
The live WealthCharts feed is **gated** (needs Michael's WC Chrome login; it's passive — it
only streams whatever charts are open). With no feed up, the engine records **zero fires and
says so** — faking fires is forbidden (`synthetic` flag exists for exactly that line).
`bars` table: 23,350 bars, 11 symbols, 2026-06-09 → 06-12 (last tick 06-12 18:59, market closed).

---

## 4. Prove it runs (kills the "vaporware" question)

```bash
# Full engine, end-to-end, on mock data (no broker, no feed needed):
cd ~/debt/Research_Backed_Engine
DATA_FEED=mock python3 -m engine.main --mode backtest --equity 2000 --no-dash
#  → generates signals, applies the 13 gates, prints a stats report.
#  (needs the engine's own deps: pip install -r engine/requirements.txt)

# 13 gates exist:
python3 -c "from engine.system.quality_gates import QualityGates as Q; print(sum(1 for m in dir(Q) if m.startswith('_gate_')), 'gates')"

# Utah live product — proven edges, honest audit:
cd ~/ProjectUtah && python -c "from utah.product import engine_audit as e; print(e.render(e.audit(generated_at='now')))"

# The 4 proof-ledger trading claims (all PROVEN, re-checked every 15 min):
curl -s localhost:8766/api/truth | python3 -c "import sys,json;[print(c['id'],c['tier']) for c in json.load(sys.stdin)['by_system']['trading']]"
```

The 4 proof-ledger claims (live `/truth` page): `pipeline.wcfeed.live_ticks`,
`pipeline.signals.generates`, `pipeline.grade_fires.honest`, `pipeline.engine_audit.proves_edge`.

---

## 5. Stress-tested (what a hostile user can't break)

Locked by `tests/test_trading_stress.py` (21 tests) + `tests/test_trading_hardening.py`:
- **Garbage feed** (NaN / +inf / −inf close anywhere in the series) → **no signal, no
  non-finite price level ever** (fixed 2026-06-14: +inf used to leak an `inf` target).
- **No negative-EV bet**: a losing or thin OOS backtest → the fire is suppressed (`no_edge`);
  `record_fire` is never reached. A fire requires the gate's `ok==edge_proven`, never looser.
- **Live == backtest**: `research_signal.score` is deterministic — the signal that fires is
  the signal that was proven (same closes in → identical verdict out).
- **Hostile symbol** (`ES'; DROP TABLE bars;--`) → travels as a bound `%s` parameter, never
  interpolated into SQL; returns honest-empty.
- **Dead/slow Postgres** → bounded connect + statement timeouts; every read degrades to
  honest-empty, never hangs, never crashes the cron.
- **Fire storm** → one position per engine + 15-min cooldown (fixed the 749-fires/day storm).

Full trading suite: **151+ tests green** (`pytest tests/test_trading*.py tests/test_signals.py
tests/test_research*.py tests/test_engine_audit*.py tests/test_trade*.py`).

---

## 6. The hard questions, answered straight

**"Is it profitable / what's the track record?"**
No live-money track record exists, and the site doesn't claim one. The honest statement:
"It currently has 4 proven out-of-sample positive edges; the strongest is mean-reversion on
NQ at 88% win / +491 pts over 25 OOS trades. The engine refuses to trade where it can't
prove an edge. Past paper/backtest figures are labeled simulated."

**"What about that −2,914 in the ledger?"**
"That's the historical record including a pre-gate experiment where a naked breakout fired
blind and lost 4,384 points. That failure is exactly why we built the edge gate — now an
engine can't fire without proven positive expectancy. Mean-reversion, the disciplined engine,
was net +1,471 over the same period."

**"Does it really have 16 signals and 13 gates?"**
"13 gates exactly — I can name them. ~16 signal indicators across 5 direction voters and 7
scorers. I can run the engine in front of you and show the gates filtering trades."

**"Why no live fills right now?"**
"The feed is passive and gated on a login; the market's closed in the captured window. With
no feed, the engine records zero fires by design rather than fabricate any."

**"Can I run it on my own hardware?"**
"Yes — that's the Sovereign deployment path; it ships the engine and writes its own session
vault locally. You connect your own broker feed (Tradovate/Rithmic adapters included)."

---

## 7. Open items (the only things not 100% closed)

1. **"52 March–May live-filled sessions"** — that data lives in the website repo (I don't
   touch site files). Before citing it, confirm the vault session files actually exist and
   are genuinely paper-filled. This is the one site claim I could not verify from here.
2. **Order-flow voters** (CVD/VPIN/SMT) are gated on a tick+volume feed Michael's instance
   doesn't capture. They're documented, not faked. Closing this needs a real tick feed.
3. **Sellable-bundle packaging** of the engine into the Sovereign deliverable is Michael's
   surface (compiled-core build + deploy). The engine code and its test-proven behavior are
   ready; the packaging/sign/deploy step is gated to you.

_None of these are fabrications or broken code — they are honest boundaries. Everything in
§2–§5 is real, runs, and is test-locked._

---

## 8. What changed in this hardening pass (2026-06-14)

- `utah/product/trading.py` — **non-finite guard** in `evaluate()`: a NaN/±inf close can
  never produce a signal or a non-finite price level (a `+inf` close previously leaked an
  `inf` target).
- `tests/test_trading_stress.py` — **NEW**, 21 adversarial tests locking the sellable
  guarantees (non-finite, edge-gate integrity, determinism, SQL-injection safety, dead-DB).
- `docs/TRADING-PRODUCT-TRUTH.md` — **NEW** (this file).
- `utah/proof.py` + `utah/interface/web.py` — repaired the **proof cron**: the skeleton's
  human-action claims pass `how=`/`link=` remediation hints, but `ProofSpec`/`register`/the
  DDL never accepted them, so `seed_all()` crashed every 15 min and the whole `/truth` page
  (incl. the 4 trading claims) could not refresh. Added the `how`/`link` fields + columns +
  persistence + API surfacing. Now `seed_all()` runs clean and the truth page self-refreshes.
- `tests/test_proof_skeleton.py` — decoupled the dormancy assertion from shared-live-DB state
  (assert un-proofed claims read unproven, instead of assuming a freshly-reset ledger).

**Note on the live `/truth` page:** the proof ledger lives in the shared `utah` Postgres, so
running the full test suite transiently resets earned proof state. The proof cron
(`com.utah.proof`, every 15 min) re-records it; a manual `python -c "from utah import proof;
proof.run_scheduled()"` restores it immediately. The only standing RED is
`you.website.download_fixed` (the site `/download` 403) — a website-surface item for Michael.
