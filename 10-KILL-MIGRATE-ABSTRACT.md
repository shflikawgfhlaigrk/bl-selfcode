# 10 — Kill / Migrate / Abstract manifest

> **Purpose:** executable cutover map from Ace (`~/.ace/`, `com.ace.*`) to Utah
> (`~/.utah/`, `com.utah.*`). Grounded in **live operator plan 21/23 pass**
> (`operator_plan.yaml`, verified `2026-06-06T08:26:18Z`) — not phantom 303/344
> sheets.
>
> **Doctrine:** Ace keeps running until Utah passes the same probe. **Migrate** =
> transplant proven code/paths. **Abstract** = keep behavior, replace
> implementation behind a Utah interface. **Kill** = delete with no Utah
> replacement (dead weight only).

---

## Legend

| Action | Meaning | Utah week-1 gate |
|--------|---------|------------------|
| **MIGRATE** | Copy/wire Ace module as-is into Utah capability; same probe must pass | Operator `evidence_command` exits 0 on Utah runtime |
| **ABSTRACT** | Reimplement behind Utah spine (Postgres, supervisor, msgspec, harness) | New probe equivalent to operator R-item |
| **KILL** | Remove from Utah; Ace may keep until cutover | N/A — must not break a passing R-probe until migrated |
| **DEFER** | Utah net-new scope; not in 21/23 baseline | Explicit new probe written before build |
| **ACE-ONLY** | Stays on Ace until Michael unblocks (hardware/auth) | — |

---

## Operator plan scoreboard (live anchor)

| ID | Title | Ace probe | Utah action |
|----|-------|-----------|-------------|
| R1 | Maps/hospital voice | ✅ | MIGRATE → voice intent + macos tool |
| R1b | Browser maps fallback | ✅ | MIGRATE → browser capability |
| R2 | Phone remote voice | ❌ blocked | ACE-ONLY → DEFER Utah mirror until Tailscale/phone hookup |
| R3 | self_code + agent_007 merge | ✅ | MIGRATE → harness self-coding lane |
| R4 | Lead scout volume | ✅ | **MIGRATE first** (revenue organ) |
| R5 | Self-code gate ready | ✅ | MIGRATE → tiered gate in Utah |
| R6 | Operator lessons | ✅ | MIGRATE → self_model + WM |
| R7 | Lawdie indexed | ✅ | MIGRATE → lawyer capability |
| R8 | Trading reflect | ✅ | ABSTRACT → WM slot (read engines) |
| R8b | Trading reflect real PnL | ✅ | ABSTRACT → engine_listener in worker |
| R9 | Gmail tasks due | ✅ | MIGRATE → mail/calendar MCP |
| R10 | Probate notices | ✅ | MIGRATE → realestate cron |
| R11 | Prediction backtest | ✅ | MIGRATE → scout sub-capability |
| R12 | Melissa/sentinel | ✅ | ABSTRACT → supervisor health + expectations |
| R13 | Growth/outbound email | ❌ | **MIGRATE + fix gate** (postal address) |
| R14 | Idea harvester | ✅ | MIGRATE → scout cron |
| R15 | Memory compaction log | ✅ | ABSTRACT → Postgres consolidator |
| R15b | HQ memory compaction HTTP | ✅ | ABSTRACT → Utah web `/api/memory-compaction` |
| R16 | Voice alarms/timers | ✅ | MIGRATE → voice intents |
| R17 | Doctor triage | ✅ | MIGRATE → doctor capability |
| R18 | Self_model operator_plan | ✅ | MIGRATE → self_model.json mirror |
| R19 | World model ≥6 slots | ✅ | **MIGRATE first** (cognition spine) |
| R-weather | Weather cache fresh | ✅ | MIGRATE → weather sensor push |

**Baseline bar for Utah cutover:** re-pass **21/21** (exclude R2). R13 must pass
with a **stricter** probe: `sent >= 1` in 24h after real postal address, not
merely "growth ran."

---

## Week-1 probes (Utah spine — before any Ace cutover)

Run against `~/.utah/` only. Ace must stay green in parallel.

| # | Probe | Pass criteria |
|---|-------|---------------|
| W1 | Postgres up | `psql $UTAH_DATABASE_URL -c 'SELECT 1'` → 1 |
| W2 | pgvector ext | `CREATE EXTENSION IF NOT EXISTS vector` OK |
| W3 | Supervisor singleton | One `com.utah.supervisor` plist; `flock` held |
| W4 | Control socket | `~/.utah/utah.sock` 0600; length-prefixed ping |
| W5 | msgspec round-trip | Struct → JSON → Postgres row → read back identical |
| W6 | Events retention | Insert 1k `agent.status`; rollup job → <100 durable rows |
| W7 | Hybrid RAG ON | `memory.hybrid_enabled=true` default; RRF returns hits |
| W8 | Operator import | Utah runs all 21 `evidence_command`s from `operator_plan.yaml` |
| W9 | Ace parallel | `ace ping` OK while Utah supervisor up (no port/socket clash) |
| W10 | Kill manifest lint | Every KILL row has signed-off rationale (this doc) |

---

## Substrate manifest

| Component | Ace path / state | Action | Utah target | Notes |
|-----------|------------------|--------|-------------|-------|
| **Primary DB** | `~/.ace/ace.db` (191 MB, 100k events/16h) | ABSTRACT | Postgres + pgvector `~/.utah/pg/` | Hot: semantic_memory, crystals, chat, registry. **Not** one-file bus. |
| **Events table** | 66k `agent.status`/day | ABSTRACT | Postgres `events` + 7d TTL + hourly rollup | Kill durable status spam first — biggest win before migration |
| **Metrics table** | 295k rows, ~7k/day | ABSTRACT | Postgres `metrics` or DuckDB ingest | 90d raw → daily aggregates |
| **sqlite-vec** | **LIVE** (10,480 vec rows, loads OK) | ABSTRACT | pgvector HNSW | Do **not** KILL on Ace today; migrate data then deprecate |
| **LanceDB knowledge** | `~/.ace/lancedb/knowledge/` active | MIGRATE → then KILL | pgvector room chunks | Voice RAG depends on this today |
| **LanceDB vault cache** | absent on disk | KILL | — | Dead path on this host |
| **gbrain HTTP** | `com.ace.brain`, crash history, Clients:0 | KILL | Postgres memory service | Dual-write already dead in daemon pool |
| **embed_cache.db** | 80 MB | ABSTRACT | pgvector or rebuildable cache | |
| **market_data.db** | 208 MB | MIGRATE | DuckDB/Parquet cold | Trading read-only |
| **Lead registry JSON** | `lead_scout_registry.json` 704K | MIGRATE | Postgres `leads.seen` + Parquet archive | At 500/day → millions; move early |
| **Vault FS** | `~/Documents/Ace Vault/` | MIGRATE | Same path or `~/.utah/vault/` symlink | Human-readable artifacts stay markdown |
| **IPC control** | `~/.ace/ace.sock` JSON-RPC newline | ABSTRACT | `~/.utah/utah.sock` length-prefix + peercred | Contract-3 methods preserved |
| **WIN binary plane** | cbor2 inert on default install | ABSTRACT | FlatBuffers + optional shm ring | Fail-loud, no silent JSON |
| **In-proc bus only** | `bus.py` daemon-only | ABSTRACT | Cross-process event broker on socket | Fixes poll→push |
| **~20 TCP engine ports** | external `~/debt/` engines | MIGRATE | Unix socket or single engine host | Do not in-process Shadow/engines |
| **File-as-channel** | dashboard JSON polls | KILL | Event push to web UI | |
| **46 launchd jobs** | `com.ace.*` | KILL | 1 `com.utah.supervisor` | Ace jobs stay until cutover day |
| **God daemon** | `daemon.py` 8783 lines | ABSTRACT | Thin orchestrator + `handlers/` | Transplant handlers incrementally |
| **Editable install** | `~/Desktop/AceOS` live | KILL (Utah) | Pinned `~/.utah/runtime/AceOS@sha` | Utah never editable-install |
| **pytest → live DB** | `ACE_TESTS_FORBID_LIVE_DB` dead | KILL behavior | `UTAH_TESTS_FORBID_LIVE_DB` enforced | Block on `~/.utah/` open |
| **API inference lane** | toggles in `toggles.json` | KILL default | CLI + MLX only; API opt-in probe | Not hard-delete until voice SLA proven |
| **SwiftUI HQ** | `Ace/` 9.7k LOC | DEFER | Utah web dashboard | Keep Ace HQ until Utah passes R15b + failures panel |
| **Vanilla dashboard** | `dashboard/app.js` | ABSTRACT | Event-pushed Svelte/React | Transplant `/api/failures` pattern first |
| **Veo / Gemini video** | dead, fake URLs | KILL | — | |
| **LTX-2 MLX reels** | not in Ace | DEFER | New probe BOX-11 | Not baseline parity |
| **Ollama fallback** | optional | KILL | MLX + Apple FM fallback | |
| **Kokoro TTS** | already erased | KILL | — | |
| **msgspec** | absent | ABSTRACT | Core object system | |
| **hybrid_search OFF** | `memory.hybrid_enabled` missing → false | ABSTRACT | ON by default in Utah | Flip + probe on Ace as pre-migration test |

---

## Capability manifest (mapped to operator plan)

### Tier 0 — migrate first (proven revenue + cognition spine)

| Capability | Ace module(s) | Action | Week-1 Utah probe |
|------------|---------------|--------|-------------------|
| World model bus | `cognition/world_model.py`, `world_model.json` | MIGRATE | R19: ≥6 slots <1h fresh |
| Lead scout frontier | `agents/lead_scout/osm_metro_frontier.py`, registry | MIGRATE | R4 + file `leads/YYYY-MM-DD.json` ≥500 list_c |
| Weather sensor | `agents/weather/` | MIGRATE | R-weather: cache <2h |
| Failure feed | `core/failure_feed.py` | MIGRATE | `/api/failures` total honest |
| Outreach sender | `~/.ace/bin/lead_outreach_send.py` | MIGRATE | R13+: `sent>=1` after real `physical_address` |
| IPC schema | `ipc/schema.py` | ABSTRACT | Contract-3 codegen parity |

### Tier 1 — migrate as capabilities (21/23 coverage)

| Capability | Ace module(s) | Action | Operator probe |
|------------|---------------|--------|----------------|
| Voice wake/STT/TTS | `voice/`, Piper, whisper | ABSTRACT | R16 + voice ping |
| Maps + hospital | `agents/operator/`, macos | MIGRATE | R1 |
| Browser NL | `agents/browser/` | MIGRATE | R1b |
| Mail + calendar + tasks | MCP gmail/calendar, `agents/tasks/` | MIGRATE | R9 |
| Realestate probate | `agents/realestate/probate.py` | MIGRATE | R10 |
| Lead enrich + contacts | `~/.ace/bin/enrich_smb_leads.py` | MIGRATE | vault `contacts.json` grows |
| Idea harvester | `agents/money_maker/idea_harvester.py` | MIGRATE | R14 |
| Prediction farm | `agents/prediction/` | MIGRATE | R11 |
| Trading commentary | `agents/trading_ooda/`, engine_listener | ABSTRACT | R8, R8b |
| Doctor | `agents/doctor/` | MIGRATE | R17 |
| Lawdie/lawyer | `agents/lawyer/`, `lawdie_index.db` | MIGRATE | R7 |
| Self-model | `agents/self_model/` | MIGRATE | R6, R18 |
| Self-coding + agent_007 | `scc/`, `cognition/claude_dispatcher.py` | MIGRATE | R3, R5 |
| Memory consolidator | `memory/consolidator.py` | ABSTRACT | R15, R15b |
| Melissa expectations | `agents/watchdog/` | ABSTRACT | R12 |
| Memory-first + RAG | `memory_first.py`, `semantic_memory.py` | ABSTRACT | hybrid ON + no-fabrication gate |
| Entity graph | `knowledge/entity_graph.py` | ABSTRACT | query_neighbors ≥3 (AGI ph1) |
| Operator plan verify | `cognition/operator_plan.py` | MIGRATE | 21/21 on Utah |

### Tier 2 — abstract onto Utah spine (no extra R-item; infra)

| Capability | Action | Why |
|------------|--------|-----|
| 92 inline IPC handlers | ABSTRACT → `handlers/` table | Testability |
| Composite 45s caps | ABSTRACT → worker pool per capability | lead_scout starvation fix |
| Scheduler crons | MIGRATE → supervisor edge-triggered | 46 jobs → 1 |
| HQ HTTP 95 routes | ABSTRACT → Utah web; poll → push | Surface honesty |
| Pushover / phone alerts | MIGRATE | G.1–G.12 |
| Observability Sentry | MIGRATE opt-in | FM-5 |

### Tier 3 — kill list (safe after Tier 0–1 migrated)

| Kill | Rationale | Blocker |
|------|-----------|---------|
| `ace.db` as event bus | Write storm | Tier 0 Postgres + retention live |
| gbrain launchd job | Dead pool + crash loop | Postgres memory service |
| cbor2 / WIN-B silent JSON | Vapor plane | FlatBuffers WIN live |
| dashboard file polls | Race/stale | Event broker |
| `com.ace.redeploy` auto hard-reset | Wiped WIP | Utah pinned deploy |
| Test pollution rows in prod DB | flaky_test etc. | Test DB enforcement |
| Duplicate Chrome profiles (4) | 7 GB RAM | One managed browser worker |
| Veo content_studio | Fake URLs | — |
| 32B/70B MLX tiers on this Mac | Timeout/OOM | L1 + CLI escalation |

### Tier 4 — defer (Utah §IV net-new — NOT in 21/23)

| Item | Action | New probe required |
|------|--------|-------------------|
| LTX-2 MLX 5+5 reels/day | DEFER | `reels_published >= 10/day` + OAuth |
| 10 profitable probate/day | DEFER | margin filter + 10/day vault file |
| 400 law-firm emails/day | DEFER | `law_firms: 400` in quotas + 400 verified/day |
| Terminal-parity harness | DEFER | IPC+CLI+git probe suite |
| 14-day AGI bar | DEFER | AGI-BLUEPRINT §1 checklist |
| Apple SpeechAnalyzer sole STT | DEFER | A/B vs whisper on your macOS build |
| Sovereign product | DEFER | separate spec |

---

## R13 special case (only failing non-blocked probe)

Ace failure is **two-layer** (verified 2026-06-06):

1. **Config:** `outreach-config.yaml` `physical_address` = Placeholder → `sent: 0`, 551 leads ready.
2. **Scheduler:** growth composite not run in 26h → operator R13 false.

**Utah manifest:**

| Step | Action | Owner |
|------|--------|-------|
| 1 | MIGRATE `lead_outreach_send.py` + suppression + ledger | engineering |
| 2 | Michael sets real postal address + sending domain | Michael |
| 3 | ABSTRACT growth from composite → dedicated cron under supervisor | engineering |
| 4 | Stricter Utah probe: `sent >= 1` in 24h, `can_cold_send: true` | probe author |

Do **not** wait for Postgres to unblock R13 — fix on Ace first, migrate proven sender.

---

## Cutover order (10 steps)

```
1. W1–W10 Utah spine probes (Postgres, supervisor, socket, retention)
2. MIGRATE world_model + weather + failure_feed (R19, R-weather, observability)
3. MIGRATE lead_scout frontier + registry → Postgres/Parquet (R4)
4. MIGRATE outreach sender; Michael unblocks postal (R13+)
5. MIGRATE realestate probate + enrich pipeline (R10)
6. ABSTRACT events/metrics retention on Ace (stop bleed) — can run before step 1
7. MIGRATE remaining Tier-1 capabilities; run 21 evidence_commands daily
8. ABSTRACT daemon spine; pin deploy checkout
9. Parallel run 7 days: Ace + Utah both pass 21/21
10. Cutover: stop com.ace.daemon; supervisor owns com.utah.*; DNS/socket flip
```

**Step 6 is the only Ace-side change allowed before Utah Postgres exists** — it
shrinks `ace.db` write rate and de-risks migration.

---

## Abstract interfaces (Utah contracts — implement before cutover)

```text
utah.db          — Postgres pool (asyncpg), pgvector, migrations/
utah.bus         — cross-process pub/sub (replaces in-proc bus.py)
utah.ipc         — length-prefixed JSON-RPC (extends Contract-3)
utah.capability  — Capability{id, schedule, run(), probe()} replaces Agent class
utah.memory      — hybrid_search ON, admission gate, one service (no gbrain)
utah.harness     — Claude SDK wrapper with tool allowlist (not raw shell)
utah.supervisor  — flock, worker pool, resource governor, circuit breaker
```

---

## What we explicitly do NOT kill

| Keep | Why |
|------|-----|
| `~/Documents/Ace Vault/` markdown | Human + agent audit trail |
| Contract-3 IPC methods | HQ + codegen depend |
| MLX L0/L1 voice path | Sub-8s voice; proven |
| `lead_scout` frontier logic | 500 NEW/run proven |
| `failure_feed` aggregation | Operator visibility |
| Inference CLI lane (`tier.L3`) | Subscription path |
| External trading engines | Off-limits; read-only bridge only |
| Frozen `core/agent.py` during migration | Extend in Utah `capability`, don't mutate Ace |

---

## Status

- [x] Manifest written against live **21/23** operator plan (`2026-06-06`)
- [ ] Foundation build-spec (Postgres schema) — maps tables from this doc
- [ ] Week-1 probes W1–W10 executed on `~/.utah/`
- [ ] R13 unblocked on Ace (postal + growth cron) before Utah outreach migrate
- [ ] 7-day dual-run gate
- [ ] Cutover sign-off (Michael)

---

*Parent: [7-UTAH-SPEC.md](7-UTAH-SPEC.md) §V. Update this file when a component
changes tier or a new operator R-item lands.*
