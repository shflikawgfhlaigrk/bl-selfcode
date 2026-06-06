# Dashboard schema map — the build contract

> Source: `ACE OS Black Gold Dashboard.zip` (built Vite SPA, title **"ACE OS —
> Local Command Deck"**, a holographic JARVIS HUD). It is a **static design
> mockup** — it fetches nothing (no `/api`, no WebSocket wired). It is therefore
> the **contract**: every program Utah builds must push its live state through
> the **control plane** to its panel. "Backend produces it → the surface
> reflects it" (no stale poll). This is item **11 (interface)**; it is preserved
> here as the target. Build the substrate first; wire panels as each program lands.

## Deck modules → backing program → how it surfaces

| Deck module(s) | Backing program (audit/phase) | Control-plane surface |
|---|---|---|
| System Spine · Local Core Daemon · Local Daemon & Socket · Control plane · Core command cockpit · Auth/Operator Gate · Tiered Cognition Router · Sync & Ingest · Sensor/WebSocket Array | **daemon spine** (2 processes · 3 ipc · 4 pids · 5 daemon) | `status` (pool, governor, uptime, pid) + the event bus (push) |
| ACE OS Core Memory · Memory Consolidation · Memory Forge · Memory Graph · Memory Holograph · Run memory consolidation | **brain** (9 rag ✅) | `tell`, `memory.recall`, `memory.consolidate`, graph reads |
| Agent Mesh · Autonomous Agent Mesh · Live Agent Runs · Live agent run monitor · Mission Stack · "who is running and what they are doing" · Code Fabricator · Prepare backend wiring patch | **agents / autonomy** (13) | `agent.run`, `agent.list`, run-event stream |
| Engine Lab · Trading Engine Lab · Engine compare · Alpha vs Shadow vs Antigrav · Market Watch · Primary chart · Tick Flow · Replay bay · WealthCharts RX · Regime scout · Trading Research Memory | **trading** (12 product · 8 binary tick plane) | tick data plane (binary) + engine state events |
| Risk Firewall · Risk Kernel · Threat Matrix · Daily Loss Cap · Execution lock · Exec Locked · Negative EV · Invalid Stop · Unscored Trade · Lunch Bleed · Risk precheck · RX only | **risk kernel** (12) | risk-state events + `risk.precheck` |
| Lead engine · Lead Qualifier · Listing Miner · Comp Analyzer · Real Estate Agents · Real Estate Agent Memory · Marketing Leads · Marketing leads pipeline · CRM Sync · Seller Outreach · Enrich lead list · Draft pitch · Scan Gulf Shores listings | **revenue: leads/probate/realestate/marketing** (12 product) | pipeline events + ledger reads |
| JARVIS HUD · JARVIS Voice Loop · Voice Pipeline · Voice Loop · Perception Layer · Assistant intelligence stream | **voice interface** (11) | `voice.*` + perception event stream |
| Audit Ledger · Black Box Ledger · Audit last block | **provenance/audit** (cross-cutting G.1–G.12) | append-only audit events |

## Status vocabulary (from the bundle)
`idle · running · pending · live · warn · error · crit · success · stop` — the
per-module health pills. Map daemon/agent/engine state to these.

## Binding implications for the substrate build
1. The **control plane is the spine of the dashboard.** Build it (item 2/3/5)
   so `status` + an event bus can carry every module's state. ✅ in progress.
2. The deck implies a **push event channel** ("Sensor/WebSocket Array"), not
   polling → 3-ipc's cross-process bus is required, not optional.
3. **Tick Flow / Market Watch** ⇒ the **binary data plane** (8-binary) is a real
   panel feed, not vapor — build the codec (done) + the data socket.
4. Every program added later (engines, leads, voice) must emit to its panel via
   the bus the moment it produces data — that is the migration (item 14) target.
