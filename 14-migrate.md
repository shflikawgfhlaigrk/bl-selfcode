# 14 — KILL / MIGRATE / ABSTRACT manifest

> What crosses into Utah. Grounded in **live operator plan 21/23 pass**
> (`~/.ace/operator_plan.yaml`, verified `2026-06-06T08:26:18Z`) — not phantom
> 303/370 sheets alone.
>
> **PORT** = lift proven Ace code, re-prove on Utah. **ABSTRACT** = same behavior,
> rewrite on Utah spine. **DISCARD** = leave in Ace; no Utah replacement.
> Nothing migrated until the live probe passes.

---

## Operator plan anchor (21/23)

| ID | Title | Ace | Utah |
|----|-------|-----|------|
| R1 | Maps/hospital voice | ✅ | PORT |
| R1b | Browser maps | ✅ | PORT |
| R2 | Phone remote voice | ❌ Michael | DEFER |
| R3 | agent_007 merge | ✅ | PORT |
| R4 | Lead scout | ✅ | **PORT first** |
| R5 | Self-code gate | ✅ | PORT |
| R6 | Operator lessons | ✅ | PORT |
| R7 | Lawdie index | ✅ | PORT |
| R8 | Trading reflect | ✅ | ABSTRACT |
| R8b | Trading reflect PnL | ✅ | ABSTRACT |
| R9 | Gmail tasks | ✅ | PORT |
| R10 | Probate | ✅ | PORT |
| R11 | Prediction backtest | ✅ | PORT |
| R12 | Sentinel/melissa | ✅ | ABSTRACT |
| R13 | Outbound email | ❌ | **PORT + fix** |
| R14 | Idea harvester | ✅ | PORT |
| R15 | Compaction log | ✅ | ABSTRACT |
| R15b | Memory compaction HTTP | ✅ | ABSTRACT |
| R16 | Alarms/timers | ✅ | PORT |
| R17 | Doctor | ✅ | PORT |
| R18 | Self_model plan | ✅ | PORT |
| R19 | World model | ✅ | **PORT first** |
| R-weather | Weather cache | ✅ | PORT |

**Utah cutover bar:** 21/21 (exclude R2). **R13+:** `sent >= 1` in 24h after real
`physical_address`, not merely "growth ran."

---

## Week-1 probes (`~/.utah/` only; Ace stays up)

| # | Probe | Pass |
|---|-------|------|
| W1 | Postgres | `psql $UTAH_DATABASE_URL -c 'SELECT 1'` |
| W2 | pgvector | `CREATE EXTENSION vector`; distance query OK |
| W3 | Supervisor | `com.utah.supervisor` + `flock` singleton |
| W4 | Control socket | `~/.utah/utah.sock` length-prefix ping |
| W5 | msgspec | struct → JSON → PG row round-trip |
| W6 | Retention | 1k `agent.status` → rollup <100 durable rows |
| W7 | Hybrid RAG | `hybrid_enabled=true` default; RRF hits |
| W8 | Operator import | 21 `evidence_command`s exit 0 on Utah |
| W9 | Parallel | `ace ping` OK alongside Utah |
| W10 | Manifest lint | every DISCARD has rationale below |

---

## Crown jewels — PORT (proven; lift + re-prove)

| Component | Ace donor | Operator | Note |
|-----------|-----------|----------|------|
| Lead frontier 500/day | `lead_scout/osm_metro_frontier.py`, registry | R4 | Postgres ledger + Parquet archive |
| Outreach sender | `~/.ace/bin/lead_outreach_send.py` | R13+ | 551 emails ready; blocked on Placeholder address |
| Probate scraper | `realestate/probate.py`, `gpn_feed.py` | R10 | 40 notices/run proven |
| World model bus | `cognition/world_model.py` | R19 | ≥6 slots <1h |
| Weather | `agents/weather/` | R-weather | cache <2h |
| Failure feed | `core/failure_feed.py` | — | transplant `/api/failures` |
| Capstone MCP | `~/.ace/capstone-mcp/` | R9,R1b | gateway re-host |
| Gmail/OAuth | `mcp/gmail.py` | R9 | Keychain pattern |
| Pushover / browser | `integrations/`, `browser/` | R1b,R17 | |
| Voice tuning | `voice/*`, `llm/local.py` | R16 | Piper-strip, Metal cap |
| Self-coding | `scc/`, `claude_dispatcher.py` | R3,R5 | harness lane |
| Lawdie | `agents/lawyer/` | R7 | |
| Doctor | `agents/doctor/` | R17 | |
| Operator plan verify | `cognition/operator_plan.py` | all | daily 21/21 |

---

## ABSTRACT (idea good; rewrite on Utah spine)

| Component | Ace | Utah target | Operator |
|-----------|-----|-------------|----------|
| Daemon 8783-line god file | `core/daemon.py` | thin orchestrator + `handlers/` | R12 |
| Events/metrics storm | `ace.db` 100k/16h | Postgres + TTL + rollup | — |
| sqlite-vec (LIVE today) | 10,480 vec rows | pgvector HNSW | R15 |
| LanceDB knowledge rooms | `knowledge/store.py` | pgvector chunks | voice RAG |
| Hybrid RAG (OFF) | `hybrid_search` toggle false | ON default + reranker | R15 |
| Memory guards | `memory_first.py` regexes | admission + provenance contract | — |
| IPC | newline JSON-RPC 64KiB | length-prefix + peercred | Contract-3 |
| WIN plane | cbor2 inert | FlatBuffers fail-loud | — |
| 46 launchd jobs | `com.ace.*` | 1 supervisor | R12 |
| Composites 45s cap | scout starvation | worker pool per capability | R4,R14 |
| HQ HTTP poll | `dashboard/app.js` | event-pushed web | R15b |
| Trading bridge | engine_listener | read-only WM slots | R8,R8b |
| Frozen contracts | dataclass+pydantic+Any | msgspec immutable | — |

---

## DISCARD (do not carry; Ace may keep until cutover)

| Kill | Why | Blocker |
|------|-----|---------|
| `ace.db` as permanent event bus | 66k status/day | Postgres + retention live |
| gbrain bun/TS | dead pool, crash loop | unified memory service |
| LanceDB vault cache (absent here) | unused | — |
| cbor2 WIN silent JSON | vapor plane | FlatBuffers |
| API lane default-on | surprise spend | CLI probe first |
| SwiftUI HQ | 2nd frontend | Utah web passes R15b first |
| Veo / fake video URLs | poisoned quality gate | — |
| 32B/70B MLX live tiers | timeout/OOM | L1 + CLI |
| 4-file dashboard polls | stale surface | event broker |
| `com.ace.redeploy` hard reset | wiped WIP | pinned deploy |
| pytest → live `ace.db` | flaky_test pollution | enforced test DB |
| 4 Chrome profiles | ~7 GB | one browser worker |

**Do NOT discard yet (live today):** sqlite-vec on Ace (works); LanceDB
**knowledge** rooms (voice RAG); external `~/debt/` engines (read-only, off-limits).

---

## DEFER (Utah §IV net-new — not in 21/23)

| Item | New probe |
|------|-----------|
| LTX-2 MLX 5+5 reels/day | `published >= 10/day` |
| 10 profitable probate/day | margin filter + daily count |
| 400 law-firm emails/day | `law_firms:400` + verified count |
| Terminal-parity harness | full IPC+CLI+git suite |
| 14-day AGI bar | AGI-BLUEPRINT §1 |
| Apple SpeechAnalyzer sole STT | A/B vs whisper on this Mac |

---

## R13 fix path (only failing non-blocked probe)

1. Michael: real `physical_address` + sending domain in `outreach-config.yaml`
2. PORT `lead_outreach_send.py` → Postgres ledger
3. ABSTRACT growth composite → dedicated supervisor cron
4. Stricter probe: `sent >= 1`, `can_cold_send: true`

Fix on Ace first; migrate proven sender to Utah.

---

## Cutover order

1. W1–W10 on `~/.utah/`
2. PORT world_model + weather + failure_feed (R19, R-weather)
3. PORT lead_scout + registry → Postgres (R4)
4. PORT outreach after postal fix (R13+)
5. PORT probate + enrich (R10)
6. **Ace-only:** events retention (stop bleed before PG migrate)
7. PORT remaining Tier-1; daily 21 evidence_commands
8. ABSTRACT daemon spine; pin `~/.utah/runtime/`
9. Dual-run 7 days: Ace + Utah both 21/21
10. Cutover: `com.ace.daemon` off; `com.utah.supervisor` on

---

## Rule

~95% of Ace 310K LOC does **not** cross. Value = crown jewels + audits 1–13.
Each PORT row needs a live probe in [15-roadmap](15-roadmap.md) before "migrated."

*Updated 2026-06-06 — operator-plan grounded.*
