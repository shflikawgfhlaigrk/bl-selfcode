# 17 — DASH audit (ACE OS Black Gold → Utah's one web surface)

> Uploaded 2026-06-06 as the target UI for Utah's single web frontend (spec §III
> Interface: "one event-pushed web dashboard; if the backend produces it, the
> surface reflects it — no stale poll"). Compiled Vite/React SPA stored at
> `dash/` (index.html + one JS bundle + one CSS bundle).

## 1. What we have
A **static demo shell**: dark "Black Gold" command deck, React 18 + Radix UI +
react-query + Recharts, Fontshare/JetBrains Mono. **No backend wiring at all** —
zero `fetch`/`ws://`/`/api/` endpoints in the bundle; every panel renders
hardcoded demo data (e.g. "Coastal Property Pros", "SHADOW-ION"). Built in
Perplexity Labs (inline-edit shim still embedded in index.html — strip at build).

## 2. Module inventory (every aspect that must wire to a real Utah backend)

| Dash module | What it shows | Utah backend it wires to | Phase |
|---|---|---|---|
| JARVIS HUD / Voice Loop (Listening/Speaking/Standby) | live voice state | interface harness: wake→STT→tell()→TTS state events | 2 |
| Assistant intelligence stream / Deliberation / Reasoning | visible thinking | agentic harness streamed trace | 2 |
| Memory Forge / Memory Graph / Memory Holograph | facts, entity graph | **brain (DONE, step 1)**: memory table, entity/mem_entity, supersede chains | 1–2 |
| Memory Consolidation ("Run memory consolidation") | sleep-time promotion | consolidate.py → ConsolidationReport | 1–2 |
| Tiered Cognition Router | intent tier routing (Apple FM/MLX/Claude) | interface intent tiers | 2 |
| Local Core Daemon / Local Daemon & Socket / System Spine | proc/supervisor health | Phase 0 spine: supervisor, control socket, worker pool | 0 |
| Sensor / WebSocket Array | slot bus (weather/calendar/mail/location/market/presence) | world-model slot bus | 2 |
| Agent Mesh / Autonomous Agent Mesh / Live Agent Runs / Mission Stack | running capabilities | worker pool job events (capabilities, not 73 agents) | 0/2 |
| Self-Coding Bay / Self-improvement queue | tiered A/B/C/D autonomy | Phase 4 bounded autonomy + provenance | 4 |
| Audit Ledger / Black Box Ledger | action provenance | event log (one rotated log → queryable ledger) | 0+ |
| Threat / Risk Kernel / Threat Matrix / Risk Firewall | gates, kill-switch | G-invariants: kill-switch, cost ledger, failure catalog | 0/4 |
| Marketing Leads / Lead Qualifier / Seller Outreach / CRM Sync | lead pipeline | Phase 3 money pipeline: Postgres ledger, Resend, enrichment | 3 |
| Real Estate Agents / Listing Miner / Comp Analyzer / Market Watch | probate/listings | Phase 3 (BOX 4/7) | 3 |
| Trading Engine Lab / Engine compare / Tick Flow / Daily Loss Cap / Exec Locked / Paper-Sim | gated trading | Phase 5 only, skin-in-game gated; WIN feed | 5 |
| Operator Gate / Auth gate / Execution lock | human gates | gate state from policy-as-data | 4 |

## 3. Binding rules for the wiring (so nothing is mocked twice)
1. **One transport:** a single WebSocket event stream from the daemon (Phase 0
   control plane exposes it); react-query for request/reply, WS push for state.
   No polling anywhere.
2. **Contract-first:** every panel gets a typed event/endpoint contract
   (msgspec Struct on the wire — same object model as IPC/PG, spec §III Objects).
3. **No demo data in prod build:** panel renders empty-state until its backend
   exists; a module with no live backend shows "not wired" honestly — never fake.
4. **Add-aspects rule:** the dash must accept new modules cheaply — module
   registry + event-namespace per module (`module.<name>.<event>`), so each
   later phase adds its panel without touching the shell.
5. Source for this build is not in repo (compiled bundle only). Phase 2 begins by
   recreating it as source (React, same look) — the bundle is the visual spec.

## 4. Status
- [x] Bundle stored at `dash/`, inventory complete (this doc)
- [ ] Phase 0: daemon event stream + System Spine panel wired first
- [ ] Phase 2: harness/voice/memory panels live
- [ ] Phase 3/4/5 panels in their phases — **never before their gate is green**
