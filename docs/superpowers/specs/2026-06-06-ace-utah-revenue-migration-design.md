# Ace → Utah Revenue Migration — Design Spec

**Date:** 2026-06-06
**Scope:** Migrate Ace's three revenue domains — **leads**, **trading**, **marketing** — into Utah, each grounded in a failure post-mortem. Equal depth, one spec, no cross-domain prioritization.
**Status:** Approved design. Next step → implementation plan (writing-plans).

---

## 0. Framing

Project Utah is a clean-room, bottom-up rebuild of AceOS. Items 1–12 of the build order are **built and live-proven** (substrate → brain → storage → interface → product ledger). This spec covers **item 14 (migration)** for the revenue surfaces only. Item 13 (autonomy) stays parked until migration is done on the proven base.

### Architecture invariant (already proven — non-negotiable)

Ace's ~60 agents do **NOT** migrate as agents. Each producer becomes a Utah **capability behind the brain** that writes the **single product-ledger schema** and lights a deck channel. This is the locked architecture (`utah/product/ledger.py`, commit `18eb265`):

```
capability  →  record_lead   → leads            → publish("leads")
            →  record_probate → probate          → publish("probate")
            →  log_outreach   → outreach_ledger  → publish("outreach")
            →  record_fire    → fires            → publish("engine"/"trading")
```

All writes are Postgres, `UNIQUE = never-twice`. Every write pushes a deck channel. Panels with no producer render **DORMANT** (honest, styled, empty) — **never simulated**.

### Binding rules carried into every domain

- **No injected/fake data, ever.** Real-or-black on every surface. A fake artifact to keep a pipeline "green" is a violation (this is precisely how marketing failed).
- **Verified outcomes, not measured activity.** Revenue is the metric, not production counts.
- **Live-wired only**; the "port + flag + not-live-wired" pattern is forbidden.
- **Everything under `~/.utah`; zero `~/.ace`.** Free-everything except the Claude CLI.
- **One thing at a time** — port → test → prove live → commit → next.
- Prove each fix with a **live probe** (5 proof artifacts standard).

### The per-domain migration template (identical for all three)

1. Port producer logic → `utah/<domain>/` module (no `~/.ace` deps, free-everything).
2. Write output → Postgres ledger via the real admission path (`record_*`, `UNIQUE` never-twice).
3. `publish(channel, event)` → deck panel lights (else DORMANT).
4. Add a daemon RPC + tests.
5. Prove with a **real** artifact + a live probe before calling it migrated.

---

## 1. Cross-cutting meta-lesson (drives the whole spec)

All three domains failed the **same** way:

> **Measured activity, not verified outcomes — compounded on an unproven base — plus fake-data shortcuts to keep pipelines "green."**

- Leads: counted leads generated, never revenue. Built 551 emails that could never send.
- Trading: counted trades fired, never edge. Fired live size on coin-flips.
- Marketing: counted reels "produced," but the artifacts were fake URLs.

Utah's antidotes are **structural**, already built into items 1–12:

| Failure mode | Utah antidote (already live) |
|---|---|
| Agent sprawl (~60 agents, drift) | Capabilities behind one brain, one ledger schema |
| Double-counting / phantom output | Ledger `UNIQUE` = never-twice |
| Fake artifacts to stay "green" | Real-or-black deck; DORMANT, never simulated |
| "Works on a branch" ≠ works | Live-probe gate, 5 proof artifacts |
| Boil-the-ocean churn | One thing at a time |

The migration's job is to **not re-introduce** these on the way in.

---

## 2. Domain — LEADS

### 2.1 Post-mortem

- **How it failed:** Ace built **551 outreach emails that could never send** — no CAN-SPAM physical address, no verified sending domain (SPF/DKIM). The success signal tracked was "leads generated," not "emails sent / revenue."
- **Why it failed:** Outreach ran as a *growth composite* that starved under the 45s composite cap; national-chain pollution diluted the no-website-SMB target until PR #784 added the filter. The money gate (postal + domain) was never the thing being worked — production was.
- **Net result:** real lead **data** (240+ at peak), **$0 earned**.
- **What we should have done:** Clear the **send** gate *before* scaling production. Revenue is the metric. A lead you can't legally cold-email is not a lead yet.

### 2.2 Utah state today

✅ **Capability LIVE + proven.** 164 real Coweta-ring no-website SMB leads in the `leads` ledger via `record_lead`. Outreach capability composes + lints + suppresses; **10 queued**; `scout_leads` / `queue_outreach` daemon RPCs + tests exist. Send is **gated** (creds + CAN-SPAM address).

### 2.3 Forward migration plan

- **L1 — Close the send gate (the actual unlock).** Michael provides: a CAN-SPAM-compliant **physical mailing address** + a **sending domain** with Resend SPF/DKIM verified. Wire the real send path (`lead_outreach_send` equivalent) → `log_outreach` on success. This is configuration + a thin sender, not new production logic.
- **L2 — Scale the frontier.** Port `osm_metro_frontier` to a Utah capability targeting 500 verified no-website SMBs/day → `record_lead`, with a Parquet archive and dedup at the ledger.
- **L3 — Deck drill-down.** Leads panel → clickable → underlying lead rows (transparency rule): name, source, contactability, outreach state.

### 2.4 Gate & probe

- **Gate (Michael):** CAN-SPAM postal address + verified sending domain.
- **Probe (success bar):** `sent ≥ 1` real email in 24h, `can_cold_send: true`, the deck leads/outreach panel reflects the real send and links to the row.

---

## 3. Domain — TRADING (paper engines + alert pipeline)

### 3.1 Post-mortem

- **How it failed:** Engines fired **live size with no proven edge** — net **−54.5 pts / 49% win-rate**. Losses concentrated on **LONG (−96)**, **RANGE chop**, and **grade-B coin-flip** signals. A whole **−168pt loss class** came from overnight-gap holds where the **per-bar stop was never checked**.
- **Why it failed:** No edge was paper-proven before risking size; regime was ignored; the 8,783-line god-file daemon + 46 launchd jobs ran the fleet under load 23.5, so failures were structural too.
- **What we should have done:** **Paper-prove an edge first.** Keep the **per-bar stop** and **regime guard** always on. Never fire real size on a coin-flip. (Recent guard work — per-bar stop + per-engine multi-regime guard — already turned the paper book positive; that is the floor Utah inherits.)

### 3.2 Utah state today

⏳ **Not built.** The `fires` ledger schema + `record_fire` exist; there is **no producer**. The whole domain is gated on a **WealthCharts login** (memory flags this as the single highest-leverage unlock).

### 3.3 Forward migration plan

- **T1 — Wire the WC feed.** Dedicated Chrome (CDP, isolated `~/.utah` profile) on the WealthCharts ES/MES chart → a `wc_cdp_bridge` capability → ticks onto Utah's **binary plane** (item-8 zero-copy packed-struct ticks). *(Upstream dependency: the binary tick plane — see §5.)*
- **T2 — Port engines as a PAPER capability.** `utah/trading/`: read-only `world_model` slots, **per-bar stop** + **per-engine regime guard** baked in (the two fixes that erased the loss classes), **paper fills only**. Each fire → `record_fire` → `fires` ledger.
- **T3 — Rich alert.** Trade-fire → Pushover with **engine + entry + exit/target/stop + a memory-grounded rationale** (the rich-alert goal).
- **T4 — Deck.** Trading panel lights on real signals → per-trade drill-down (the "why" behind each fire).

### 3.4 Hard rule

**NO real orders.** Paper-only until an edge is proven in Utah on real WC ticks. This is the migration target chosen for this round (paper engines + full alert pipeline), not live execution.

### 3.5 Gate & probe

- **Gate (Michael):** WealthCharts login + ES/MES chart loaded. **Upstream:** binary tick plane wired.
- **Probe:** a real WC tick → engine `SIGNAL_CHECK` → a **paper** fire written to `fires` → deck trading panel updates **and** a Pushover alert fires with the full entry/exit/stop + rationale.

---

## 4. Domain — MARKETING (deferred, with an explicit resume trigger)

### 4.1 Post-mortem

- **How it failed:** The BlackLabel auto-post pipeline (engine.fired → Veo reel → quality gate → IG/TikTok) broke when **Veo billing died (429, credits depleted)**. A **fake-URL fallback** then fed the quality gate — a **fake-data violation** that poisoned the surface. The pipeline was also wiped once by an agent_007 tree reset, and added a second frontend (churn).
- **Why it failed:** It depended on a **paid external generator with no local fallback**, and chose to **emit a fake artifact** rather than go black when the generator died.
- **What we should have done:** **Real-or-black.** Render locally where we control the output. Never emit a fake artifact to keep the pipeline "green."

### 4.2 Utah state today

⏸️ **Deferred** (Michael's call this round). The marketer is additionally IG/TikTok-cred-gated. The deck marketing panel stays **DORMANT** (honest, empty) until the resume trigger fires.

### 4.3 Resume trigger (so "defer" is not open-ended)

Resume marketing **only** when **all three** hold:

- **(a)** Leads earns **≥ $1 real revenue** (the verified-outcome bar — marketing follows money, not precedes it), **AND**
- **(b)** A **proven local video path** exists — LTX-2/MLX or ffmpeg compositing — **no Veo, no fake URLs**, **AND**
- **(c)** IG/TikTok credentials provided.

### 4.4 Plan when triggered

`utah/marketing/` capability → render a **real** local video file → quality gate runs on the **real file** (real-or-black) → store the artifact in the ledger → auto-post **gated on creds** → channel `marketing`. Until then: panel DORMANT.

### 4.5 Gate

Deferred by decision **+** IG/TikTok creds **+** a proven local renderer. No code this round; the resume trigger above is the contract.

---

## 5. Baseline dependencies ("still working on the differences")

These are **upstream prerequisites**, not in-scope migration work — flagged so the plan sequences correctly:

- **WIN binary tick plane** (item 8) — **Trading T1 depends on it.** Ticks must land on the zero-copy packed-struct plane, not an ad-hoc path.
- **The "forever" gap** — launchd boot-persistence (`com.utah.*`) + the web bridge under the supervisor. Until wired, a reboot or `utah stop` leaves the stack down. Revenue capabilities that must run unattended (lead frontier, trading observer) depend on this for true reboot-survival.

Trading and unattended leads both assume these are closed first; the implementation plan must order them ahead of the dependent steps.

---

## 6. Out of scope (this spec)

- Item 13 autonomy / self-coding expansion (parked until migration done).
- Live order execution for trading (paper-only this round).
- Marketing implementation (deferred; only the resume trigger is specified).
- The ~48 remaining Ace agents that are integration-gated (calendar/contacts/notes/stripe) or trivial personal-utility helpers — out of the revenue scope.

---

## 7. Success definition

Migration of a domain is **done** only when:

1. The capability writes **real** artifacts to the product ledger via the real admission path (`UNIQUE` never-twice).
2. The deck panel reflects them live (or is honestly DORMANT where gated).
3. A **live probe** passes with a **real** artifact (the per-domain probe in §2.4 / §3.5).
4. It's committed — "done" = merged + works live, not a branch with green tests.

Leads and trading each carry a Michael-gate (postal+domain; WC login). Until those open, the capability is built, tested, and **DORMANT-honest** behind the gate — never faked green.
