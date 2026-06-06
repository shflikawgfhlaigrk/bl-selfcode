# UTAH — AGENT HANDOFF (2026-06-06)

> Read this first, then `19-stop-at-13.md`, `17-brain-proof.md`, `18-spine-proof.md`,
> `dashboard/SCHEMA-MAP.md`. This is the fast-start. Everything below is built and
> **live-proven on this Mac** or is an explicitly-flagged TODO. Nothing is faked.

## 0. TL;DR
Project **Utah** = a clean-room, bottom-up rebuild of AceOS ("Ace, finally fixed,
on a baseline that works"). We build the substrate **in audit-number order, prove
each layer against Ace, full live wiring, no injections**, and STOP at item 13
(autonomy). **Items 1–12 are DONE + proven.** Code at `~/Desktop/ProjectUtah`,
runtime at `~/.utah`, **zero `~/.ace`**. Python 3.14, Postgres+pgvector, Claude
CLI as the only paid lane.

## 1. Build order & status (the spine of the plan)
```
1 programs   ✅ stack locked (requirements.lock, 106 deps), 13G models, isolated
2 processes  ✅ bounded anyio pool + governor (os.getloadavg admission) + supervisor
3 ipc        ✅ unix control socket: 5-byte len-prefix frame + LOCAL_PEERCRED + JSON-RPC; + event bus
4 pids       ✅ flock singleton (auto-release on crash) + verified hard-exit (os._exit)
5 daemon     ✅ THIN orchestrator (boot DAG → run_forever) + dispatch table (no god-file)
6 objects    ✅ msgspec Structs/enums (WriteResult/WriteDecision/Hit/Reply/ConsolidationReport)
7 utah-spec  ✅ 7-UTAH-SPEC.md synthesis (reconciled with built spine)
8 binary     ✅ zero-copy packed-struct ticks/PCM codec (fail-loud, not Ace's dead cbor2)
9 rag/brain  ✅ THE BRAIN — proven > gbrain + Ace (17-brain-proof.md)
10 storage   ✅ pg MVCC primary + DuckDB OLAP (postgres_scanner ATTACH)
11 interface ✅ event bus + starlette web bridge (deck + /status + SSE) + honest deck
12 product   ✅ Postgres revenue ledger (UNIQUE never-twice; publishes to bus)
--- STOP HERE (item 13 = autonomy) ---
14 migration  ⏳ NEXT: port Ace tools so each DORMANT deck panel gets a real producer
13 autonomy   ⏳ after migration, on the proven base
```

## 2. Architecture / code map (3,424 LOC, 33 modules, package `utah`)
**Brain** (`utah/`): `core.tell` = recall→ground→reason→remember. `memory.py` =
pure decision fns (`decide_write`/`rrf_fuse`/`compute_decay`/`passes_gate`) +
`StoreBackend` Protocol + `PostgresStore` (ONE managed conn, reconnects, no fd
leaks). Hybrid recall = pgvector HNSW dense + Postgres FTS sparse → RRF(k=60) →
fastembed cross-encoder rerank → entity boost → **no-fabrication answer gate**
(sim≥0.45 ∧ overlap≥0.50). Writes: admission-gated (ALLOWED_SOURCES), dedup@0.995,
supersede@0.90 paraphrase OR @0.78 shared-entity ("Newman" fix). `brain.py` =
`claude -p` subprocess (injectable `set_runner`), `is_refusal()`, `extract_facts`
returns `None` on brain-down (retry, never drop). `embed.py`/`rerank.py` = fastembed
ONNX, injectable, fail-loud. `consolidate.py` = sleep-time promotion (no side door).

**Spine** (`utah/daemon/`): `frame`(wire) `codec`(binary) `rpc`(JSON-RPC2) `peercred`
(owner-only) `pool`(CapacityLimiter) `governor`(load/inflight admission, OVERLOADED)
`bus`(pub/sub, drop-on-overflow) `dispatch`(Context + table) `handlers/core_handlers`
(ping/status/tell/agent/publish/memory_stats/shutdown) `server`(unix listener +
streaming `subscribe`) `lifecycle`(flock + verified-exit) `daemon`(thin orch)
`supervisor`(liveness-probe restart, backoff+circuit-break, reap) `client`/`cli`.

**Storage** `store/olap.py` (DuckDB read-only ATTACH). **Product** `product/ledger.py`
(leads/probate/outreach_ledger/fires, UNIQUE=never-twice, emits to bus).
**Interface** `interface/web.py` (starlette/uvicorn :8766) + `interface/static/live.html`
(the honest deck).

## 3. Live state RIGHT NOW
- **Postgres:** PostgreSQL 17.10 + pgvector 0.8.2, cluster `~/.utah/pgdata`, `127.0.0.1:5433`
  + socket `/tmp/.s.PGSQL.5433`, DB `utah`. Tables `memory`(20 rows, vector(384),
  HNSW `memory_hnsw` + GIN `memory_fts`), `entity`(22), `mem_entity`(41).
- **Processes:** `postgres`(59898) · `utah.daemon.supervisor`(86186) · `utah.daemon.daemon`
  (86189) · `utah.interface.web`(90979). Daemon socket `~/.utah/run/utahd.sock`.
- **Deck:** `http://127.0.0.1:8766/` = honest deck (real or DORMANT, never simulated);
  `/sim` = Black Gold design mockup; `/status` `/memory` `/events`(SSE) `/api/tell`.
- **Brain memory:** 20 facts incl. 15 self-knowledge rows ("Utah's datastore is
  Postgres+pgvector…") — the brain can describe its own build.

## 4. Run / verify (commands)
```
~/Desktop/ProjectUtah/bin/utah start|stop|status|ping|tell "…"   # supervised stack
PYTHONPATH=~/Desktop/ProjectUtah ~/.utah/venv/bin/python -m utah.interface.web   # bridge :8766
cd ~/Desktop/ProjectUtah && ~/.utah/venv/bin/python -m pytest tests/ -q          # 138 + 11 integration
```
Proofs (all green): brain 134 tests + beats gbrain/Ace; **spine gate 9/9**; storage
3/3 (MVCC **32 writers 0 locks** vs SQLite 29/32 storm); bus pub/sub; web SSE push;
ledger 6/6. Integration tests need a disposable DB: `UTAH_TEST_DSN="host=/tmp port=5433
dbname=utah_test"` (test creates+truncates it; NEVER point at `utah`).

## 5. NEXT (item 14 migration — the pattern)
Each DORMANT deck panel needs a real producer. The pattern per Ace tool:
1. Port the tool as a `utah/<domain>/` module (no `~/.ace` deps, free-everything).
2. Write its output to the Postgres **ledger** (`product/ledger.py`) via the real
   admission path (UNIQUE never-twice).
3. On each real artifact, `publish(channel, event)` to the bus → the deck panel
   lights up (channels: leads/probate/outreach/engine/risk/voice/agent/trading).
4. Prove with a real artifact (a real scraped lead, a real probate record) — NO fakes.
**Input-independent first** (scrapers/enrichment/engine-observer — no business inputs).
**Money-flow blocked on Michael:** Resend sending domain + SPF/DKIM · CAN-SPAM postal
address · TikTok/IG creds · WIN market feed.

## 6. Key decisions this session
- **Thin orchestrator is correct** (thin coordination core + fat hardened modules;
  NOT a god-file). Robustness/throughput live in the modules + pool + binary plane.
- **Wire LIVE only, no injectable stand-ins at wiring points; prove via live probe.**
- **Dashboard:** the Black Gold `.zip` is a minified "frontend simulation" (its own
  footer) — values are hardcoded, no source available. We built an **honest deck**
  (`live.html`): real data where wired, **DORMANT (empty, styled)** where no producer,
  never simulated. Black Gold kept at `/sim` as the design reference.
- **MCP: capstone-only.** Claude Code config trimmed to `mcpServers={capstone}` (the
  AceOS gateway at `~/.ace/capstone-mcp/run`; reaches 129 servers via `mcp_call` on
  demand). gbrain/chrome-devtools deleted; 5 plugin MCPs + 6 claude.ai connectors cut;
  plugins 71→12. Use capstone, not 5000 servers. (Connectors are account-side → `/mcp` to finish.)

## 7. Gotchas / traps (don't rediscover these)
- **AF_UNIX path > ~104 chars fails** on macOS → tests use short `/tmp/utXXXX/d.sock`,
  NOT pytest `tmp_path`. Real socket `~/.utah/run/utahd.sock` is fine.
- **`~/.claude.json` is live-rewritten** by the running Claude Code process → edits
  (esp. `claudeAiMcpEverConnected`) get clobbered; `settings.json` is stable. Edit
  Claude config, then have the user RESTART.
- **Verbose "I don't know" leak:** `core.tell` MUST use `brain.is_refusal()` (prefix
  match), not `!= I_DONT_KNOW` — else verbose refusals get stored as turns. Fixed.
- **gbrain CLAUDE.md (~300KB) auto-loads** if you read files under `~/gbrain` — don't.
- **pgvector insert:** `register_vector(conn)` + pass `pgvector.Vector(list)`.
- **Lockfile/pidfile are never deleted** (stale-safe; liveness = a `ping` probe).
- **One managed conn per process** in `PostgresStore` (killed Ace's fd-leak class).
- **utah_test disposable DB** is the test/proof sandbox; the live `utah` DB holds only
  real seed + self-knowledge (keep it clean; purge any test pollution you create).

## 8. The "forever" gap (only thing between supervised and literally-forever)
Daemon is supervised (auto-restart proven). NOT yet wired: (a) the **web bridge is
unsupervised**, (b) **no launchd boot-persistence** (`com.utah.*`) — a reboot or
`utah stop` leaves the stack down until `utah start`. Wire a launchd job + put the
bridge under the supervisor for true reboot-survival. (Deferred process/deploy substrate.)

## 9. Binding rules (Michael — non-negotiable)
- **No injected/fake data, ever.** Real-or-black on every surface. Prove every fix
  with a live probe (5 proof artifacts standard). "Done" = merged + works live.
- **Live-wired only**; the "port+flag+not-live-wired" pattern is forbidden.
- **Everything under `~/.utah`; zero `~/.ace`.** Free-everything except the Claude CLI.
- **Build in order, prove each vs Ace, STOP at 13.** Skipping steps = days of rework.
- **One MCP (capstone).** Be terse, action-oriented, execute first.

— end handoff —
