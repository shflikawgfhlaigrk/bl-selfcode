# Ace / Project Utah — Code Grade Audit

**Session:** 2026-06-14 (initial pass) → **2026-06-14 deep read** (corrected)  
**Scope:** All **153** `utah/*.py` modules, **330** test files, **26** launchd plists, `interface/static/live.html`, daemon RPC registry, import/wiring graph.  
**Method:** Full module catalog (LOC + docstring), grep-backed live vs test-only wiring, launchd→module map, deck poll path traced (`/status`, `/state`, `/panel/*` — **not** `operator.json`).  
**Bar:** Anthropic-grade production code — small modules, explicit boundaries, honest health signals, no god-files, capabilities that advertise themselves must actually route.

**Companion docs (living registries):**

| Doc | Purpose |
|-----|---------|
| [`JUMBLE-FLAGS.md`](JUMBLE-FLAGS.md) | Running list of jumbled / oversized / misleading code — **add a flag here before shipping messy diffs** |
| [`../ops/utah-file-grades.json`](../ops/utah-file-grades.json) | Machine per-file index (`python3 ops/utah_grade_index.py`) |
| [`../ops/grade-queue.json`](../ops/grade-queue.json) | Open weaknesses queue |
| [`ACE-STRESS-FINDINGS.md`](ACE-STRESS-FINDINGS.md) | Adversarial stress synthesis — question bank, failure taxonomy, fix waves, architecture thesis |
| [`ACE-ARCHITECTURE-PROBES.md`](ACE-ARCHITECTURE-PROBES.md) | Layer-by-layer probe catalog — 78% pass rate, ops/proof + product IPC gaps |

---

## Executive summary

| Area | Grade | One-line verdict |
|------|-------|------------------|
| **Architecture (vs old Ace)** | **A−** | Modular daemon, capability routing, brain loop — the anti–god-file lesson landed |
| **Core loop (`core` + `brain` + `router`)** | **A−** | Clear recall→ground→reason→remember; PERSONA/NO_FAB split is professional |
| **Daemon / IPC** | **A−** | Table-driven dispatch (`dispatch.py`), 20 RPC methods, validated async handlers; `panels.py` 448 LOC |
| **Interface (web deck + voice)** | **C** | Real deck (`live.html`) polls daemon — but **`operator.json` / `outcome_gate` never reach the UI**; `web.py` 917 LOC |
| **Product / revenue pipeline** | **B+** | Ledger UNIQUE constraints + **15 revenue launchd crons** wired; 6 product files ≥650 LOC |
| **Memory** | **B+** | Layered store/logic/pipeline; `codebase.ingest` cron indexes source |
| **Self-code / SICA** | **B−** | `com.utah.selfcode` live; spread across 8+ modules; **`sica_selfaudit` has no launchd job** |
| **Tests & verification** | **C** | ~330 test files; **`pytest --collect-only` fails** without `psycopg`; grade index 100% heuristic |
| **Ops / proof / canary** | **B+** | 26 launchd jobs mapped; `foundation.json` + `operator.json` written; proof ledger + `/truth` |
| **Capability honesty** | **C** | **`introspect.CAPABILITIES` overstates** connectivity/advisory; deck omits outcome scoreboard |

**Overall:** Utah’s **spine is production-grade** (daemon, core loop, ledger, launchd mesh). Gaps are **UI truth wiring** (outcome gate invisible on deck), **false capability advertising** in introspect, **24 files ≥400 LOC**, and **audit tooling** (heuristic grades, missing dev deps).

---

## Corrections vs initial pass (2026-06-14)

The first audit used `ops/utah-file-grades.json` heuristics and stale v2 “orphan” caps **without tracing imports**. Deep read corrections:

| Prior claim | Verdict | Evidence |
|-------------|---------|----------|
| `operator_app.py` / `permissions.py` are orphans | **WRONG** | `operator.run()` calls `ensure_app()` → `operator_app.ensure()` every 5 min (`com.utah.operator`); Ace.app launcher runs `python -m utah.permissions bootstrap` |
| `courier.py` is orphan | **WRONG** | `alerts.py` routes non-push delivery through `courier.deliver()` |
| `product/news.py` is orphan | **WRONG** | `core._capability_reply` dispatches `Route.NEWS` → `news.answer()` (lines 182–190) |
| `introspect.py` is orphan | **WRONG** | `core._self_model_facts()` calls `introspect.self_model()` on self/project turns |
| BLA-418: deck mis-reads `mail_gated` | **WRONG FRAMING** | Deck **never reads** `~/.utah/run/operator.json` — polls `/status`, `/memory`, `/state` only (`live.html` `poll()` ~825) |
| Outcome gate is on the machine but visible | **HALF TRUE** | `operator.run()` writes `outcome` from `revenue_heal.outcome_gate()` to `operator.json` (line 318) — **not consumed by deck or `/state`** |
| v2 human grades merged | **WRONG** | `~/Desktop/grades/v2/` contains only `utah-complete-index.md`; `utah-core.md` / `utah-tests.md` absent |

### Truly unwired (grep + router verified)

| Module | Listed in introspect? | Live prod importers | Fix |
|--------|----------------------|---------------------|-----|
| `utah/connectivity.py` | Yes | **Tests only** | Add `Route.CONNECTIVITY` + core dispatch OR remove from CAPABILITIES |
| `utah/advisory.py` | Yes | **Tests only** | Wire to router/persona command OR remove from CAPABILITIES |
| `utah/fm_local.py` | No | `proof_skeleton` only | Part of Part VI harness plan — not live inference yet |
| `utah/sica_selfaudit.py` | jobs_status label | **`__main__` only; no `com.utah.selfaudit.plist`** | Add launchd job or call from `sica_autonomy` cycle |

### Live wiring evidence (sample)

| Module | Production call chain |
|--------|----------------------|
| `operator_app` | `com.utah.operator` → `operator.ensure_app()` → `operator_app.ensure()` |
| `permissions` | Ace.app double-click → `-m utah.permissions bootstrap`; `operator.permissions_status()` reads result |
| `courier` | `alerts._deliver()` → `courier.deliver(via=…)` |
| `news` | `router.route()` → `Route.NEWS` → `core._capability_reply` → `news.answer()` |
| `introspect` | `core._build_context` → `_self_model_facts()` → `introspect.self_model()` |
| Revenue crons | 26 plists under `ops/launchd/` → `utah.product.*.run_scheduled`, `utah.mail`, `utah.operator`, etc. |

### Scale facts (all 153 `utah/*.py` files read for LOC)

- **34,171** total LOC in `utah/`
- **6** files ≥650 LOC · **24** files ≥400 LOC
- Largest: `interface/web.py` (917), `product/ledger.py` (844), `product/leads.py` (832), `integrations/wc_feed.py` (790), `voice/loop.py` + `core.py` (740 each)

---

## What “Ace” is in this repo

- **Utah** = the system (daemon, Postgres, capabilities, deck at `:8766`).
- **Ace** = the persona Michael talks to (`core._conversation_context` labels assistant turns `"Ace:"`; `brain.PERSONA` defines behavior).
- **Capabilities** live under `utah/product/`, `utah/integrations/`, `utah/voice/`, plus hot-loaded **`agents/*.py`** (single-file extensions).

The intended loop (well implemented in `utah/core.py`):

```
tell → router → (agents | local | capability) → brain (Claude CLI) → memory store
```

Failure modes degrade honestly (`ReplySource.UNAVAILABLE`, no fabrication) — this is **Anthropic-grade intent** even where individual files still need splitting.

---

## Strengths (keep these patterns)

1. **Module docstrings that state contracts** — e.g. `brain.py` (refusal detection), `agents.py` (hot-load safety), `ledger.py` (UNIQUE = product integrity).
2. **Injectable boundaries for tests** — `brain.set_runner`, `core` introspect injection, selfcode git timeout wrapper.
3. **Honest gating vocabulary** — `mail.py` returns `{ok, gated}`; operator sweep exposes `mail_gated` separately from `mail_ok`.
4. **Daemon decomposition** — `utah/daemon/{server,rpc,dispatch,handlers,supervisor}.py` vs Ace’s 8k-line `daemon.py`.
5. **Router as pure table** — `utah/router.py` documents retired tiers and escalation to brain.
6. **Proof ledger** — outcome verification as first-class (`utah/proof.py`, `/truth` deck page).
7. **Grade infrastructure** — `ops/utah_grade_index.py` encodes RUBRIC-V2-STRICT caps (secrets, shell=True, hardcoded paths, swallowed exceptions, oversize files).

---

## JUMBLE-FLAGS (summary)

Full registry with IDs and owners: **[`JUMBLE-FLAGS.md`](JUMBLE-FLAGS.md)**.

| ID | Severity | Location | Issue |
|----|----------|----------|-------|
| J-001 | **High** | `utah/interface/web.py` (~917 LOC) | HTTP app, middleware, 20+ routes, legacy aliases, proof API, tell/stream, static mounts — **one file** |
| J-002 | **High** | `utah/voice/loop.py` (~740 LOC) | Wake pipeline + DSP (AGC/HPF) + mic health + loop — tuning constants inline |
| J-003 | **Medium** | `utah/config.py` (~581 LOC) | Env seams, CAN-SPAM, email normalization, voice paths, wake model paths — **config god-file forming** |
| J-004 | **Medium** | `utah/product/{leads,ledger,outreach,trading}.py` | 600–850 LOC each — scout + SQL + business rules combined |
| J-005 | **Medium** | Deck routes in `web.py` | `/`, `/deck`, `/live`, `/classic`, `/sim`, `/hud` — overlapping aliases, stale React HUD vs live.html |
| J-006 | ~~orphan chain~~ | **closed — false positive** | See Corrections table; all four modules are live |
| J-011 | **open** | High | Deck never reads `operator.json` | Wire `outcome` + `mail_gated` into `/state` or `/panel/operator`; spine must not show all-green when `outcome.ok` is false |
| J-012 | **open** | Medium | `utah/introspect.py` `CAPABILITIES` | Lists `connectivity`, `advisory` with no router/core path | Route or remove from capability list |
| J-013 | **open** | Medium | `utah/sica_selfaudit.py` | `__main__` references `com.utah.selfaudit` but **no plist exists** | Add launchd job or invoke from `sica_autonomy` |
| J-014 | **open** | Low | `utah/fm_local.py` | Proof-only; not on live inference path | Track in UTAH-SPEC Part VI; don't grade as broken orphan |

**Rule going forward:** Any PR that adds >400 LOC to a single file, introduces a third legacy alias, or wires a capability only in tests **must** add a row to `JUMBLE-FLAGS.md` before merge.

---

## Subsystem deep dives

### 1. Brain + core loop — **A−**

**Files:** `utah/core.py`, `utah/brain.py`, `utah/router.py`, `utah/local_brain.py`

**What works**

- Conversation thread shared by voice and chat (`_CONVO` deque).
- Self-model grounding for introspection questions (`_self_model_facts`).
- Code hits separated in context block (fixes “oauth.py isn’t in my context”).
- Refusal prefixes anchored to start — prevents soft-refusal memory poisoning.

**Improve**

- `core.py` at 740 LOC is approaching jumble threshold — extract: context builder, learn-on-miss web fetch, stream presentation.
- `LOCAL_HEAVY` still in `Route` enum though retired — document or remove from public enum.

### 2. Daemon + supervisor — **B+**

**Files:** `utah/daemon/*`

**What works**

- Control socket CLI (`utah/daemon/cli.py`) with explicit `_CTL_DOWN` exception set.
- Handler split (`core_handlers.py`, `panels.py`) vs monolith.

**Improve**

- `panels.py` (448 LOC) — split panel fetchers per domain (revenue, voice, failures).
- Document single source of truth for `status` RPC shape (deck + introspect both consume it).

### 3. Interface — **C+**

**Files:** `utah/interface/web.py`, `utah/interface/static/*`, `11-interface.md`

**What works**

- Live deck (`live.html`) is the real operator UI; comments in `build_app()` say so explicitly.
- Origin guard middleware for cross-origin tell.
- Streaming tell + thinking split aligned with `brain.split_thinking`.

**Improve (priority)**

- Split `web.py` into: `routes/deck.py`, `routes/api_tell.py`, `routes/proof.py`, `middleware.py`, `app.py`.
- Deprecate `/sim` → `/hud` with redirect + single comment in spec, not six aliases.
- Execute `11-interface.md` plan: unified harness, Apple STT path — track in UTAH-SPEC Part VI, not a new doc.

### 4. Voice — **B−**

**Files:** `utah/voice/*`

**What works**

- Two-stage wake documented with false-fire lesson.
- Mic liveness, AGC, HPF — production-hardening comments cite real incidents (2026-06-13 restart storm).

**Improve**

- Extract DSP chain (`agc.py`, `hpf.py`) and constants table from `loop.py`.
- TTS/STT module boundaries already exist — keep loop orchestration-only.

### 5. Product / revenue — **B+**

**Files:** `utah/product/*`, `utah/mail.py`, `utah/revenue_heal.py`, 26× `ops/launchd/*.plist`

**What works (verified live)**

- `ledger.py` — schema-level dedupe (`UNIQUE (name, region)`, `UNIQUE (recipient, campaign)`).
- Launchd mesh: leads, leads-maps, enrich, outreach, probate, probate-enrich, probate-outreach, marketer, signals, grade-fires, engine-audit, wcfeed, brief, mailcheck, replies.
- `core_handlers.ledger_snapshot` feeds deck `/state` with lab feed gate truth (`trading.feed_available()`).
- `revenue_heal.outcome_gate()` — real send/sale window check (tested in `tests/test_outcome_gate.py`).

**Improve**

- Split 600–850 LOC product modules (J-004).
- **Wire outcome gate to deck** (J-011) — post-mortem rule #1 is in `operator.json` but invisible on `live.html`.
- Fix introspect false ads (J-012).

### 6. Memory — **B+**

**Files:** `utah/memory/*`

Clean layering per `memory/__init__.py`. Keep admission pipeline as the only write path.

**Improve:** Ensure `test_memory_init.py` eval/exec cap is intentional or refactor test harness.

### 7. Self-code + SICA — **C+**

**Files:** `utah/selfcode.py`, `utah/sica*.py`

**What works**

- Kill switch, automerge flag, bounded git, smoke log separated from failures feed.
- Documented reward-hack history in specs.

**Improve**

- Consolidate autonomy policy into one module with explicit state machine diagram.
- Never add another probe file target without banning it in `sica_goals.py` (learned lesson).

### 8. Agents (hot-load) — **A−**

**Files:** `utah/agents.py`, `agents/*.py`

Small, focused loader — **this is the model** for extensibility. More capabilities should look like this instead of 800-line modules.

### 9. Tests — **C**

**Observed:** `python3 -m pytest tests/ --collect-only` → `ModuleNotFoundError: No module named 'psycopg'`.

**Improve**

- Add `requirements.txt` or `pyproject.toml` with dev extras documented in README.
- Re-link human v2 grades from `~/Desktop/grades/v2/` so `utah-file-grades.json` is not 100% heuristic.
- Fix or delete `fake_test` flagged files: `test_proof_scheduled.py`, `test_proof_schema.py`.

### 10. Ops / proof — **B**

**Files:** `utah/proof.py`, `ops/verify.py`, launchd plists

`proof.py` heuristic cap: `shell=True` — review and replace with explicit argv list (security + grade).

---

## Anthropic-grade checklist (project-wide)

Use this on every touched file:

| Criterion | Pass? | Notes |
|-----------|-------|-------|
| Single responsibility | Partial | Hot paths fail (web, voice loop, config) |
| File < 400 LOC (or justified) | Partial | 10+ production files exceed |
| Public API documented in module docstring | **Yes** | Strong culture |
| Errors typed, not swallowed | Mostly | grep shows few bare `except: pass` |
| Timeouts on subprocess/network/DB | Partial | tasks.py, olap.py caps documented |
| No hardcoded `/Users/...` in production | Partial | `operator_app.py` Ace.app path shims |
| Health signals honest | **Improving** | outcome_gate, mail_gated |
| Tests prove failure paths | Partial | grade-queue lists gaps (outreach, calendar) |
| No orphan modules | **Mostly** | J-006 closed (false positive); true unwired: connectivity, advisory, sica_selfaudit |
| Legacy paths quarantined | Partial | ~/.ace shims |

---

## Prioritized improvement roadmap

### P0 — honesty & operator truth (REAL after deep read)

1. **Wire outcome gate to deck** (J-011): extend `_deck_state()` / `live.html` poll to read `operator.json` or add `/panel/operator` — substrate green + `outcome.ok=false` must read red/yellow on spine.
2. **Restore v2 human grades** (J-009): commit `ops/grades-v2/utah-core.md` in-repo or restore Desktop source files.
3. **Dev deps** (J-010): `psycopg` (and peers) so `pytest --collect-only` works on fresh clone.
4. **Fix capability advertising** (J-012): route `connectivity` + `advisory` or drop from `introspect.CAPABILITIES`.

### P1 — de-jumble hot paths (next 2 weeks)

1. Split `interface/web.py` (J-001).
2. Split `voice/loop.py` DSP vs orchestration (J-002).
3. ~~Wire or remove orphan modules (J-006)~~ — **cancelled** (false positive after import trace).

### P2 — product module sizing

1. Extract shared SQL/connection helpers from product giants.
2. Cap `config.py` growth — move voice tuning to `utah/voice/settings.py`.

### P3 — interface spec execution

1. Track Apple STT + unified harness in UTAH-SPEC Part VI only.
2. One deck URL canonical path; redirect others.

---

## How to flag jumble in docs (ongoing process)

1. **Before merge:** If the change increases file size past 400 LOC, adds a legacy alias, or leaves capability unwired → add row to [`JUMBLE-FLAGS.md`](JUMBLE-FLAGS.md) with:`ID`, `file`, `symptom`, `recommended split`, `owner`.
2. **Weekly:** Run `python3 ops/utah_grade_index.py`; triage `ops/grade-queue.json`.
3. **Session audits:** Append dated section below (do not create `*AUDIT*` files outside this doc — per UTAH-SPEC, audit *findings* append here; spec changes append to UTAH-SPEC).

---

## Session log

### 2026-06-14 — Deep read correction pass

- Cataloged all **153** `utah/*.py` modules (34,171 LOC); mapped **26** launchd plists to entry modules.
- Traced deck poll path: `live.html` → `/status`, `/memory`, `/state` — **does not read `operator.json`**.
- Corrected false orphan claims (operator_app, permissions, courier, news, introspect).
- Identified true unwired: `connectivity`, `advisory`, `sica_selfaudit` (no plist), `fm_local` (proof-only).
- Updated Linear BLA-418/415; canceled BLA-420 (J-006 false positive); added [BLA-433](https://linear.app/black-label-bots/issue/BLA-433) (J-012), [BLA-434](https://linear.app/black-label-bots/issue/BLA-434) (J-013).

### 2026-06-14 — Live probe pass

- Merged runtime findings as **J-015–J-020** (Discord mock webhooks = **P0 critical**); Linear **BLA-427–432**.
- Neural coordination train persisted via CLI ([Probe Ace + neural train](128e8bc4-bff3-4c04-8b35-e38026ecef98)).

### 2026-06-14 — Daemon/deck stress pass ([Stress daemon RPC panels](d796e72c-7df3-4ef9-860a-300e79726938))

- **Infrastructure honesty ~68%:** RPC/panels fast and ledger-faithful; deck/operator/proof **overstate health** ($0 sales + 269 sends = green; 115k Discord failures invisible to heal loops).
- Filed **J-021–J-027**, Linear **BLA-435–439**, **BLA-447** under BLA-415.
- Converges with live probe: fix Discord flood (J-015/016) + outcome on deck (J-011/BLA-418) + activity vs revenue gate (BLA-435).

### 2026-06-14 — Product/revenue stress pass ([Stress product revenue paths](09a9c338-82b1-4b32-a258-278867db7d89))

- Machine is **busy** (269 sends/26h, ledger UNIQUE clean) but **not outcome-verified** ($0 sales still green).
- Router misses: `"did we send mail"` / `"trading edge"` → local brain instead of grounded mail/engine panels.
- **J-028–J-033**, Linear **BLA-440–446**; overlaps BLA-435/442 on `outcome_gate` activity-vs-outcome split.

### 2026-06-14 — Memory/introspect stress pass ([Stress memory introspect traps](7ec348de-a270-4c91-980a-1cf1d079c544))

- Admission/refusal/agent isolation **mostly hold**; **profile + `memory.answer` poisonable** (fake CAN-SPAM beats real address).
- Agent keyword hijack (`disk_free`, `git_state` on metaphor turns); `CAPABILITIES` overclaims vs router.
- **J-034–J-038**, Linear **BLA-448–452**; purge stress rows **42048–42050** before outreach.

### 2026-06-14 — Stress synthesis ([Synthesize stress test intel](7a4e140d-f9ec-45ed-96ba-6fd6a7119e7c))

- **`docs/ACE-STRESS-FINDINGS.md`** — 18-probe bank, 8-class taxonomy, Waves 1–3, outcome-verified intelligence thesis.
- **18** STRESS Linear issues (BLA-435–452) + live probe BLA-427–432 under BLA-415.
- Linear **free-tier cap** blocked `[STRESS]` epic creation — create manually and reparent STRESS children.

### 2026-06-14 — Architecture probe suite ([Architecture probe stress suite](b5d5de7d-c8d2-48bd-ba20-18cdab70a11d))

- **78%** probe pass (68/87); data socket, deck, memory, voice, selfcode green; **ops/proof red** (`verify.json` 600s timeout; canary stack overflow on Discord path).
- Product work RPCs timeout @5s; launchd plist drift; no CI for verify/canary.
- **J-039–J-047** in `JUMBLE-FLAGS.md`; Linear `[PROBE]` epic blocked (same cap).

---

*Next P0: J-015/016 + canary recursion fix (J-040) → BLA-448 → BLA-418/435/442. See ACE-STRESS-FINDINGS Wave 1 + ACE-ARCHITECTURE-PROBES ops layer.*
