# 7 — UTAH-SPEC (the final, configured masterpiece)

> The one synthesized spec. The six audits (`1-programs` … `6-objects`) are the
> evidence — each in the format **What we had · Why we did it · What we didn't
> think about · What we're gonna change · How it helps**, from the dirt + web
> (sourced). This doc is the synthesis: the root cause, the binding rules, the
> baseline, and the had→change→why-better across the whole base. **Planning only —
> nothing committed or installed. Utah is entirely separate from Ace** (own
> `~/.utah/` runtime, `com.utah.*`, never touches `~/.ace`/`ace.db`).

---

## I. The single root cause (why Ace failed)

> **Ace measured activity — commits, PRs, "SHIPPED+LIVE", 4,758 branches — not
> verified outcomes (works for Michael, makes money), and compounded features on a
> base never proven able to carry them.** $0 revenue in 6 weeks, 310K LOC, no
> trading edge, leads gated behind a `physical_address:"Placeholder"`.

Accountability (mine): I optimized *inside* the frame instead of questioning it;
when asked to "optimize every process" I made a rotten foundation faster instead
of saying it couldn't compound. Utah fixes the scoreboard: **nothing is "done"
without a live probe; baseline first, then compound.**

## II. Binding ground rules

1. **Utah = personal; Sovereign = the sold product.** Commercial-license limits
   (e.g., F5-TTS CC-BY-NC) bind Sovereign, not Utah.
2. **Utah does a ton daily** → **Postgres + pgvector** is the primary store
   (concurrent OLTP at volume); DuckDB = OLAP.
3. **Baseline = every Ace capability, done right, locked — THEN compound.** (§IV.)
4. **Michael never opens a terminal again** → Utah's unified voice+chat surface is
   a **full frontier agentic harness** (Claude Agent SDK / `claude -p` + real
   shell/file/git/web/subagents + visible thinking). MLX = fast voice; heavy turns
   auto-escalate. It must do everything this terminal does.
5. **Capabilities, not agents.** Ace's 73 agents are a capability inventory, not a
   build target. Implement each as the simplest unit (tool/MCP/cron/function);
   consolidate hard. Bar = **fully autonomous partner.**
6. **Apple Intelligence is in-scope:** SpeechAnalyzer (STT), Foundation Models
   (free on-device ~3B for classify/extract/intent), Personal Voice (clone option).
7. **Free-everything except the Claude CLI; locality is irrelevant (2026-06-06).**
   Cost is the only constraint. The **Claude CLI (subscription) is the one allowed
   paid lane = the operator/agentic brain.** Everything else is **$0** — a free
   cloud tier *or* local, whichever is best/simplest; **no per-token paid APIs**
   (the Anthropic API lane stays dead). **"Local-first" is retired** — the best
   *free* tool wins, cloud or on-device. Resolves: agentic brain = Claude CLI ✓;
   embeddings = best *free* (torch/BGE is fine — locality no longer a constraint) ✓;
   **de-prioritize the local-model squeeze** (thermal ratchet / 2 GiB Metal cap /
   speculative-decoding tuning are no longer "crown jewels" — Claude + free tools
   carry the load).

## III. The base, synthesized — had → change → why better

| Layer | What we had | What we change | Why better | Audit |
|---|---|---|---|---|
| **Programs** | py3.14, SQLite+sqlite-vec+LanceDB, MLX+API lane, whisper.cpp, Veo(dead), 2 frontends, gbrain | Postgres+pgvector, drop sqlite-vec/Lance, MLX+Apple FM (no API), SpeechAnalyzer/Moonshine + F5/StyleTTS2, LTX-2 MLX, one web UI, kill gbrain, uv.lock | −36 GB, #1 crash gone, STT 3–6× faster, no surprise spend, −9.7k LOC | [1](1-programs.md) |
| **Processes** | ~120 resident procs, 46 KeepAlive jobs, load 23.5, no supervisor | one supervisor + bounded worker pool, edge-triggered (no KeepAlive), resource governor, backoff+circuit-break | ~120→~10 procs, load under cores, crash storms contained | [2](2-processes.md) |
| **IPC** | 6 mechanisms, inert WIN(cbor2), 20 TCP ports, file-as-bus, 64 KiB cap | control socket (len-prefix+peercred) + WIN flatbuffers/shm + cross-proc bus | 6→3, dead→live binary plane, 20→1 ports, poll→push | [3](3-ipc.md) |
| **PIDs** | pidfile lock + SIGTERM-drain; zombie/trap outages | flock + verified hard-exit + supervisor-reaps + liveness=probe | zombie/down-state eliminated; clean auto-recovery | [4](4-pids.md) |
| **Daemon** | 8,783-line god-file, sync-on-loop, no structured concurrency | thin orchestrator + modular spine + AnyIO + worker pool | loop-lag 2–4 s→~0; testable; bounded footprint | [5](5-daemon.md) |
| **Objects** | dataclass+pydantic+`Any` bags, mutable, drift-crash | msgspec.Struct core (immutable, one model) + pydantic at edge | 2–5× serialize, drift→type error, one model IPC/WIN/PG | [6](6-objects.md) |
| **Binary** | struct float32 blobs, PCM, WIN-B(cbor2) **inert**, JSON elsewhere | FlatBuffers/packed-struct (zero-copy) WIN, fail-loud; pgvector/Arrow at rest; JSON control-only | binary plane actually runs; zero-copy reads; exact floats | [8](8-binary.md) |
| **RAG** | BGE+sqlite-vec(brute-force,load-fails)+FTS5; hybrid built but **OFF**+dead BM25; 2 RAGs | pgvector HNSW + Postgres FTS → RRF (ON) + cross-encoder rerank; structural no-fabrication; 1 service + GraphRAG | real HNSW (no crash), hybrid actually fuses, confabulation killed at admission | [9](9-rag.md) |
| **Storage** | one ace.db (191MB), single-writer lock storms, sqlite-vec/Lance | Postgres+pgvector primary (MVCC) + DuckDB OLAP — **verified live** | concurrent writers at volume; native vectors/FTS/JSON; pgvector 0.8.2 smoke-passed | [10](10-storage.md) |
| **Interface** | 2 brains, 14B/512-tok recite-only, sandboxed tools, hidden thinking | unified voice+chat **agentic harness** (Claude+tools+subagents+visible) + Apple SpeechAnalyzer/FM | does everything the terminal does; sub-8 s natural voice; free Apple STT/tier | [11](11-interface.md) |
| **Product** | leads $0 (placeholder gate), reels unposted, Veo dead, trading $0/no-edge | open the gates: Postgres ledger+Resend+enrich; LTX-2 video+posting; inbox triage; trading decoupled/skin-first | the money actually flows; real posted video; inbox handled; honest trading | [12](12-product.md) |
| **Autonomy** | KeepAlive churn (4,758 branches), reset-tree deploy, red gate | bounded edge-triggered on a green baseline; tiered A/B/C/D + kill-switch; outcome-gated | compounds instead of churns; can't reset tree / self-edit safety | [13](13-autonomy.md) |

## IV. Baseline feature catalog (capabilities Utah must cover — then compound)

**Runtime gate (authoritative):** live `operator_plan.yaml` — **21/23 pass**
(2026-06-06); Utah cutover requires **21/21** excluding R2 (phone). R13 needs a
stricter send probe after postal address is real.

**Planning union (reference only):** ACE_EXECUTION_TASK_SHEET **303**,
ACE_MASTER_PLAN **370** (not 344), ACE_MASTER_PLAN_V2 **63** P-tier items,
AGI-BLUEPRINT, AGI-BUILD-ROADMAP 13 BOXes. See
[14-migrate.md](14-migrate.md) for per-component Kill/Migrate/Abstract (operator
plan **21/23** grounded). Each capability must pass its live probe before "compound."

- **Interface:** unified voice+chat agentic harness; wake→STT→same loop; visible
  thinking; does everything the terminal does.
- **Memory (compounding):** per-turn promotion, hybrid recall (pgvector+FTS),
  admission gate + no-fabrication, contradiction/supersede, decay, entity graph,
  sleep-time consolidation; DuckDB analytics over it.
- **World-model & cognition:** slot bus (weather/calendar/mail/location/market/
  engines/presence), sensor push, cognition tick.
- **Revenue:** 500 no-website SMB/day + 400 verified law-firm emails (BOX 7);
  10 profitable probate/day (BOX 4); 5+5 reels/day with a real posting pipeline +
  quality video via LTX-2 MLX (BOX 11); per-fill trade alert <5 s + 17:00 brief,
  engines decoupled (BOX 3).
- **Composites' capabilities** (concierge/doctor/growth/lawyer/operator/realestate/
  scout/sentinel/steward/trading/weather) — covered as tools/crons, not 73 agents.
- **Autonomy (compounds on baseline):** goal-gen (4 passes), skill library, tiered
  self-coding A/B/C/D, 14-day AGI bar.
- **Surfaces:** one event-pushed web dashboard; **if the backend produces it, the
  surface reflects it** (no stale poll).
- **Infra/invariants:** supervisor, WIN plane, verified-exit, one rotated log,
  stash-verify-swap deploy, G.1–G.12 (every fact sourced, cost ledger, phone <5 s,
  failure catalog, engine-death alert, kill-switch smoke test).

## V. Build order & status

**SPEC — COMPLETE** (every audit from the dirt + web, sourced; 4-AI-audit-ready):
- [x] Substrate: `1-programs · 2-processes · 3-ipc · 4-pids · 5-daemon · 6-objects · 8-binary · 9-rag`
- [x] Layers: `10-storage · 11-interface · 12-product · 13-autonomy`
- [x] Plan: `14-migrate` (Kill/Migrate/Abstract) · `15-roadmap` (Phase 0 → finish, gated)
- [x] `7` — this synthesis (root cause · ground rules · baseline catalog · had→change→why)

**PROVISIONING — DONE & VERIFIED** (see §VII): `~/.utah` env + venv (101 pkgs,
py3.14, all chosen libs import) · Postgres+pgvector **0.8.2 smoke-tested** (:5433) ·
13 GB models copied · macOS 26 → Apple Speech/FM available.

- [ ] **CODE — Phase 0 spine — GATED.** Per the goal + the brainstorming HARD-GATE,
  **we STOP here**: nothing is coded until Michael approves the spec.

## VI. Index (16 documents)

**Audits:** [1-programs](1-programs.md) · [2-processes](2-processes.md) ·
[3-ipc](3-ipc.md) · [4-pids](4-pids.md) · [5-daemon](5-daemon.md) ·
[6-objects](6-objects.md) · [8-binary](8-binary.md) · [9-rag](9-rag.md) ·
[10-storage](10-storage.md) · [11-interface](11-interface.md) ·
[12-product](12-product.md) · [13-autonomy](13-autonomy.md)
**Plan:** [14-migrate](14-migrate.md) · [15-roadmap](15-roadmap.md)
**Final:** **7 — this file (synthesis).**

> Doctrine: the audits hold the evidence; this file is the single source of truth.
> New substrate research = a new numbered audit; the synthesis updates here.
> Nothing else is a spec.

## VII. Provisioning status (done & verified 2026-06-06; nothing coded yet)

| Item | Status |
|---|---|
| `~/.utah/` runtime root (models/logs/vault/bin/config/pgdata/run) | ✅ created |
| Utah venv (Python **3.14**) | ✅ 101 pkgs; **all chosen libs import** (msgspec, mlx, mlx_lm, mlx_whisper, sounddevice, onnxruntime, piper, openwakeword, psycopg, pgvector, asyncpg, mcp, fastapi…) |
| **Postgres + pgvector** | ✅ isolated cluster `:5433`, `utah` db, `vector` 0.8.2 — **distance-query smoke PASSED** |
| Models (copied from Ace, not shared) | ✅ 13 GB — Qwen 1.5B/14B, Llama-8B, whisper, piper (32Bs dropped) |
| macOS 26.5.1 | ✅ Apple **SpeechAnalyzer** + **Foundation Models** available |
| ffmpeg · node · whisper-cpp · ollama | ✅ already present (Homebrew) |

**Deferred downloads (intentionally not pulled now):**
- **F5-TTS / StyleTTS2** (natural TTS, GB-scale, Phase 2) — pull at build.
- **LTX-2 MLX video** (~19–42 GB, Phase 3/5) — too large to auto-pull; explicit go needed.
- **Cross-encoder reranker** — ✅ PULLED 2026-06-06: fastembed ONNX `ms-marco-MiniLM-L-6-v2`, **no torch** (corrects the prior "pulls torch" note — `utah/rerank.py` is fastembed-ONNX; torch is not imported by the rerank path), durable at `~/.utah/models/fastembed` (87M); RAG rerank now ON (real scores, relevant-ranked-first proven, full suite green).
- **mlx-whisper / Moonshine** STT models — auto-download on first use (runtimes installed).
- **Resend sending domain + API key** — business decision (not a download).
- **Embedding model** — finalize MLX/Apple-FM choice at build (avoid torch).

Everything installable that the foundation needs is in place; the deferred items
are giant/torch/credential-gated and pull at their phase. **No Utah code written.**
