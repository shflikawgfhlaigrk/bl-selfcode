# GROUND RULES & BASELINE (binding — set by Michael 2026-06-06)

These four supersede anything earlier that conflicts. They are the frame the
entire audit/spec runs against.

1. **Utah is PERSONAL. Sovereign is the SOLD product.** Utah is Michael's own
   system; it can use best-of-breed tools regardless of commercial license.
   Commercial-license constraints apply only to **Sovereign**.
2. **Utah does a *ton* daily** — high-volume, many concurrent writer pipelines,
   always-on, holds everything. This is concurrent OLTP at volume, not a light
   single-user app.
3. **Baseline = EVERY Ace feature, locked and done right — THEN compound.** The
   system isn't "started" until the full feature catalog (below) is live and
   correct. Autonomy/self-improvement compounds *on top of* that baseline.
4. **Michael will NEVER open Claude in a terminal again — everything is through
   Utah.** Utah's interface must do everything this terminal does.

## CORRECTIONS that supersede earlier sections

- **Storage (supersedes §A "memory = SQLite"):** Utah's **primary store is
  PostgreSQL + pgvector.** Rationale (audit-grade): high daily write volume +
  many concurrent agent/pipeline writers = real multi-writer OLTP → Postgres MVCC
  (no single-writer bottleneck/`database is locked` — Ace's exact failure).
  **pgvector** unifies vectors (HNSW) + relational + JSONB + FTS in one engine for
  a system that holds every feature; **LISTEN/NOTIFY** can back durable
  cross-process events. **DuckDB** stays the OLAP/analytics tier (can read
  Postgres/Parquet). SQLite drops to, at most, tiny local config — not the hot
  path. Local Postgres over a unix socket keeps latency low; it's always-on
  anyway, so the "resident server" cost is moot.
- **TTS license (supersedes §B correction):** F5-TTS's CC-BY-NC is **fine for
  Utah (personal)** — use it for best naturalness + cloning. The
  commercial-license constraint (→ StyleTTS2/Kokoro/Piper) applies to **Sovereign**.

## THE AGENTIC HARNESS — Utah's defining baseline capability

Because Michael never opens a terminal again, **Utah's unified voice+chat surface
(§6.6) IS a full agentic harness** — the operator brain:

- A **frontier reasoning agent** (Claude via the Agent SDK / `claude -p` with the
  complete tool surface) embedded as Utah's main loop, able to **run shell,
  read/write files, use git, web-search/fetch, spawn subagents, and build / audit
  / fix Utah and everything else** — driven by voice or text.
- The **local MLX models** handle fast voice/chitchat/classification; any turn
  needing investigation, coding, ops, analysis, or self-inspection **auto-escalates
  to the agentic harness** (intent-tiered, §6.6) with the full thinking + tool +
  subagent trace streamed to the surface (like Cursor/Claude).
- **Test of done:** anything in this very session — "audit your own stack from the
  dirt," "run web research," "build feature X," "why did Y fail" — Michael can ask
  Utah by voice/chat and it does it, visibly. If it can't, the baseline isn't met.
- The **self-coding safety tiers (A/B/C/D)** govern what the harness may change
  autonomously vs. with approval; off-limits stays off-limits.

---
