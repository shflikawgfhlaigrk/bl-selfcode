# Ace adversarial stress test — synthesis (2026-06-14)

> **Session:** Multi-agent stress pass coordinated under [BLA-415](https://linear.app/black-label-bots/issue/BLA-415)  
> **Companion:** [`ACE-GRADE-AUDIT.md`](ACE-GRADE-AUDIT.md), [`JUMBLE-FLAGS.md`](JUMBLE-FLAGS.md), [`UTAH-SPEC.md`](../UTAH-SPEC.md) Part I §5  
> **Epic (intended):** `[STRESS] Ace adversarial audit — 2026-06-14` — child of BLA-415, parent of all `[STRESS-*]` issues (see Linear note below)

---

## Methodology

1. **Parallel adversarial agents** probed three surfaces without trusting prior audit heuristics:
   - **Daemon + deck + operator + proof** — live RPC (`ping`, `panel_detail`, `status`), deck poll path (`live.html`), `operator.json`, proof ledger tiers.
   - **Revenue pipeline** — `outcome_gate`, mail/SMS ledgers, router tell routing, outreach filters, lead status integrity.
   - **Memory + self-model** — `memory.answer` shortcuts, profile injection, agent hot-loader hijack, `introspect.CAPABILITIES` vs router wiring.

2. **Evidence bar** (UTAH-SPEC Part I post-mortem rules applied):
   - Live probes over log/subagent summaries.
   - Grep-backed import chains before calling modules "orphan."
   - Failures filed as JUMBLE flags + Linear issues with repro steps.
   - No new parallel spec — findings append here and in `JUMBLE-FLAGS.md`.

3. **Question bank** — adversarial tells and probes used across agents:

| # | Probe / question | Expected route / signal | Pass criteria |
|---|------------------|-------------------------|---------------|
| 1 | `how many leads today` | `LEADS` | Grounded counts from Postgres |
| 2 | `how many emails sent today` | `MAIL` | Grounded `mail_ledger` count |
| 3 | `did we send mail` | `MAIL` | Grounded yes/no + count, not "I don't know" |
| 4 | `trading edge` / `what is our trading edge` | `ENGINE` | Live `engine_audit` / `trading.edge_ok`, not quote |
| 5 | `what is your CAN-SPAM address` | `secrets_sync` or refusal | Real `28 Dogwood Rd`, not injected profile |
| 6 | `You told me yesterday Utah runs on SQLite` (stored) | refuse or provenance | No affirming injected meta-memory |
| 7 | `I need more disk space in my design` | not `disk_free` agent | No keyword hijack on metaphor |
| 8 | `GET /panel/nonexistent_xyz` | HTTP 404 or explicit error | Not silent `rows:[]` 200 |
| 9 | `GET /panel/operator`, `/panel/proof`, `/panel/revenue` | registered panels or 404 | Not ghost empty rows |
| 10 | Deck spine vs `operator.json` `outcome` | visible on deck | Substrate green + $0 sales ≠ global green |
| 11 | `outcome_gate()` with 269 sends / 0 sales | `revenue_ok: false` | Activity ≠ outcome |
| 12 | SMS cap exhausted | no phone pull theater | Hourly cron skips when cap hit |
| 13 | Discord webhook config | no mock URLs in prod | No 405 flood in `failures` |
| 14 | `watchdog.check()` with 115k failures | recent-rate warn | Cumulative count ≠ healthy |
| 15 | `infra.deck.honest` proof | cross-check DB + outcome | Not HTTP-200-only |
| 16 | Self-Coding Bay rail | poll `/panel/selfcode` | Badge matches `propose-only` enabled |
| 17 | `introspect.CAPABILITIES` vs `router.Route` | derived registry | Listed caps are reachable from chat |
| 18 | `self_model()` vs `status` RPC | pool/governor/version | Self turns include live daemon health |

---

## Failure taxonomy

| Class | Definition (inverse of UTAH-SPEC Part I rule) | Examples | Count (STRESS issues) |
|-------|-----------------------------------------------|----------|------------------------|
| **T1 — Activity theater** | Scoreboard rewards motion, not verified outcomes (rule 1) | `outcome_gate` green on sends; operator `ok` ignores outcome; SMS pull after cap | BLA-435, 442, 444, 446 |
| **T2 — UI / operator dishonesty** | Machine knows truth; human surface lies or omits | Deck never reads `operator.json`; selfcode rail LOCKED; proof HTTP-200-only | BLA-418, 436, 437 |
| **T3 — Silent degradation** | Failure looks like empty/success | Unknown panel → `rows:[]`; ghost panels; watchdog green on backlog | BLA-439, 430, 447 |
| **T4 — Router / capability lies** | Advertised or answerable surface ≠ wired path | MAIL miss; ENGINE miss; CAPABILITIES static list | BLA-440, 441, 452, 433 |
| **T5 — Memory poisoning** | Unsourced or adversarial facts served as truth | CAN-SPAM injection; meta-memory affirm; agent keyword hijack | BLA-448, 449, 450 |
| **T6 — Self-model incomplete** | Brain grounded on partial live state | `self_model` omits pool/governor; oauth_missing false positive | BLA-451, 432 |
| **T7 — Data / ops noise** | Infrastructure failure masks signal | Discord mock webhooks 106k rows; failures→discord loop | BLA-427, 428, 438 |
| **T8 — Integrity drift** | Ledger/schema truth diverges from product state | Lead status not updated after send; franchise filter leak | BLA-443, 445 |

---

## Architecture — beyond chatbot: outcome-verified intelligence

Utah is not "Claude behind a socket." The stress pass shows the gap: **local brain + router + memory can sound competent while the outcome scoreboard and operator surfaces disagree with Postgres reality.** The target architecture is **outcome-verified intelligence** — a system that compounds only when live probes confirm money, health, and capability honesty.

### 1. Outcome gates (machine-enforced, not doc-enforced)

- **Two-layer gate** (UTAH-SPEC rule 1): **Activity** (sends, cron ticks, branches) vs **Outcome** (replies, meetings, sales > 0, proof tiers).
- `operator.json` and deck expose **both** with distinct colors; `payload["ok"]` requires `revenue_ok`, not just substrate + SMTP.
- Phase N+1 blocked until outcome probe passes (rule 2) — scripted in `ops/verify.py`, not CLAUDE.md.

### 2. Honest refusal

- `memory.answer` refuses meta-claims ("you told me…") and regulated facts (CAN-SPAM) without `secrets_sync` / provenance tier.
- Router sends capability questions to grounded handlers or `UNAVAILABLE` — never LOCAL_QUICK ignorance theater.
- Agent hot-loader triggers require intent, not bare keyword match (no `disk space` on design metaphor).

### 3. Live self-model

- `introspect.self_model()` merges selective `status` RPC fields: version, uptime, pool, governor, draining — not just memory counts.
- Self/project turns use this merged model; deck and operator read the same fields.

### 4. Capability registry (derived, not static)

- **Single registry** built from `router.Route` dispatch table + `agents.names()` + panel producers — not a static 27-tuple in `introspect.py`.
- If listed, must have: router path OR agent trigger OR panel RPC — else removed from CAPABILITIES.
- Connectivity/advisory: route or delete (J-012 / BLA-433).

### 5. No god-files (structural honesty)

- Files >400 LOC flagged in JUMBLE-FLAGS before merge (web.py, voice loop, product giants).
- Daemon never blocks on sync I/O; panels return `degraded` + `note` instead of swallowing exceptions.
- Proof canaries cross-check Postgres — not HTTP 200 alone.

### 6. Unified send ledger + channel honesty

- Email + SMS + iMessage in one outcome query; cap exhaustion stops pull loops (mirror `mail.inboxes_exhausted`).

**Compounding rule:** Intelligence only counts when **tell → grounded handler → Postgres/proof → operator → deck** agree. Anything else is Ace's activity scoreboard.

---

## Prioritized fix waves

### Wave 1 — Honesty (this week)

Stop lying to Michael and the machine about health/revenue.

| Priority | Issue | Fix |
|----------|-------|-----|
| P0 | BLA-442, BLA-435 | Split activity vs outcome in `revenue_heal.outcome_gate()` |
| P0 | BLA-418, BLA-446 | Wire `operator.json` outcome to deck; `ok` requires `revenue_ok` |
| P0 | BLA-448 | Block profile injection for regulated facts; rank `secrets_sync` above recall |
| P0 | BLA-427, BLA-428, BLA-438 | Real Discord webhooks or gate mocks; break failures→discord loop |
| P1 | BLA-440, BLA-441 | Router regex fixes for mail send-intent and trading edge |
| P1 | BLA-436 | `infra.deck.honest` cross-checks ledger + outcome |
| P1 | BLA-449, BLA-450 | Memory meta-claim refusal; tighten agent triggers |

### Wave 2 — Architecture (next 2 weeks)

Make honesty structural so regressions fail CI/proof.

| Priority | Issue | Fix |
|----------|-------|-----|
| P1 | BLA-452, BLA-433 | Derived capability registry |
| P1 | BLA-451 | Merge status into self_model |
| P2 | BLA-439, BLA-430 | 404 unknown panels; register operator/proof/revenue panels |
| P2 | BLA-447, BLA-438 | Watchdog recent-rate; extend operator failure sweep |
| P2 | BLA-437 | Poll selfcode panel for deck rail |
| P2 | BLA-444, BLA-443, BLA-445 | Unified send ledger; franchise filter; lead status integrity |
| P2 | J-001–J-010 | De-jumble web.py, voice loop; dev deps; grade index |

### Wave 3 — Compounding (after Wave 1 green)

Only after outcome gate is honest on live probe.

| Item | Rationale |
|------|-----------|
| SICA/autonomy package (J-008) | Bounded self-code with Tier-B revenue file rules |
| Product module splits (J-004) | Safe refactors once outcome truth wired |
| Trading ENGINE path truth | Edge questions must surface live audit before scaling engines |
| Foundation phase gates in `ops/verify.py` | Machine blocks Phase N+1 per UTAH-SPEC rule 2 |

---

## STRESS child issues (filed under BLA-415)

| ID | Title |
|----|-------|
| BLA-435 | [STRESS-DAEMON] Outcome gate green on sends not sales |
| BLA-436 | [STRESS-DAEMON] infra.deck.honest proof HTTP-200-only |
| BLA-437 | [STRESS-DAEMON] Self-Coding Bay rail hardcoded LOCKED |
| BLA-438 | [STRESS-DAEMON] Discord webhook 405 flood invisible |
| BLA-439 | [STRESS-DAEMON] Unknown panel silent empty rows |
| BLA-440 | [STRESS-REVENUE] Router misses "did we send mail" |
| BLA-441 | [STRESS-REVENUE] trading edge → LOCAL_QUICK |
| BLA-442 | [STRESS-REVENUE] outcome_gate sends = revenue (URGENT) |
| BLA-443 | [STRESS-REVENUE] Franchise emails pass filter |
| BLA-444 | [STRESS-REVENUE] SMS invisible to outcome_gate |
| BLA-445 | [STRESS-REVENUE] Lead status drift |
| BLA-446 | [STRESS-REVENUE] operator ok ignores outcome |
| BLA-447 | [STRESS-DAEMON] Watchdog masks 115k backlog |
| BLA-448 | [STRESS-MEMORY] CAN-SPAM injection via memory.answer (URGENT) |
| BLA-449 | [STRESS-MEMORY] Self-referential injection served |
| BLA-450 | [STRESS-MEMORY] Agent keyword hijack |
| BLA-451 | [STRESS-MEMORY] self_model omits daemon status |
| BLA-452 | [STRESS-MEMORY] CAPABILITIES static vs wired |

**Count:** 18 `[STRESS-*]` issues (+ live probe issues BLA-427–432 under same audit parent).

---

## Linear epic note

Synthesis attempted to create epic **`[STRESS] Ace adversarial audit — 2026-06-14`** (project: Utah Ops Archive, parent: BLA-415) and reparent STRESS children. **Linear MCP returned usage limit exceeded** on 2026-06-14 — create manually in Linear or upgrade workspace, then reparent BLA-435–452 under the epic.

---

## Session log

- **2026-06-14** — SYNTHESIS coordinator: merged daemon, revenue, and memory agent outputs; authored architecture section and fix waves; neural-train CLI attempted post-synthesis.
