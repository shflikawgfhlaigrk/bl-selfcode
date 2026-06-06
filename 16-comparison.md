# 16 — Ace vs Sovereign vs Utah (which is better, per dimension)

> Honest three-way: **Ace** (the failed original, running), **Sovereign** (the
> clean-room sellable rebuild, base built), **Utah** (this spec, designed +
> provisioned, not yet coded). Best = on the merits, with reality noted.

| Dimension | Ace | Sovereign | Utah (spec) | Best |
|---|---|---|---|---|
| Foundation discipline (gates) | doctrine in docs only; ran on unproven base | foundation-first, real | foundation-first + post-mortem-driven + gated | **Utah** |
| Daemon / spine | 8,783-line god-file | clean ~300-line daemon (**built**) | modular + AnyIO + worker pool (designed) | **Utah** ceiling / Sovereign **built** |
| Storage | SQLite-everything, lock storms | SQLite (fine, light) | **Postgres+pgvector, verified** for volume | **Utah** |
| Memory / no-fabrication | confabulated; hybrid broken | **NO_FABRICATION proven** (simple) | pgvector hybrid+RRF+rerank+admission gate | **Utah** ceiling / Sovereign **proven** |
| IPC + data plane | 6 mechanisms, inert WIN | clean NDJSON socket (no data plane) | control + WIN(flatbuffers/shm) + bus | **Utah** |
| Object model | 3 systems, drift-crash | clean, simple | msgspec one-model (immutable) | **Utah** |
| Interface (agentic) | 14B recite, no tools, hidden thinking | brain = any CLI, clean (not a full harness) | **full Claude agentic harness + visible thinking** | **Utah** |
| Revenue features (real) | real leads/probate **data** but **$0** | **none** (base only) | ports Ace's real pipelines + opens the gates | **Utah** |
| Autonomy | churn (4,758 branches) | PR-only, half-built/off | bounded, tiered, on-green-baseline | **Utah** |
| Process / supervision | 46 KeepAlive, load 23.5 | few, simple | one supervisor + worker pool | **Utah / Sovereign** |
| Maintainability (LOC) | 310K, god-files | **~4K, auditable in an afternoon** | modular (will grow; unbuilt) | **Sovereign** |
| **Works TODAY** | runs (fragile, $0) | base runs (no features) | **spec + provisioned env, not coded** | **Ace / Sovereign** |
| Sellability | no (personal mess) | **yes (white-label / rebrand)** | no (personal) | **Sovereign** |
| Cost model | local + silent API spend risk | local, BYO brain | **free + Claude CLI only** | **Utah / Sovereign** |
| Scale (a *ton* daily) | single-file ceiling | single-user light | **Postgres MVCC** for volume | **Utah** |

## Verdict
- **Utah is the best PLAN** — it fuses **Ace's real features** with **Sovereign's
  discipline** and goes deeper (Postgres+pgvector, the agentic harness, WIN binary
  plane, msgspec, hybrid RAG). Best on ~12/15 dimensions on the merits.
- **Honest caveat: Utah isn't built.** **Sovereign's ~4K-LOC clean base RUNS today**
  with **proven** no-fabrication memory; **Ace runs today** with real revenue *data*
  (on a rotten base, $0). Utah is a spec + a provisioned env — its ceiling is
  highest, its current reality is lowest.

## The move this implies (resolves open-item #4)
**Utah should START from Sovereign's proven clean base, not rebuild the spine from
zero.** Sovereign already paid for a clean daemon + grounded NO_FABRICATION memory
+ hot-loaded agents + a safety gate. Graft onto it: **Postgres+pgvector, the WIN
data plane, msgspec objects, the Claude agentic harness, and the ported Ace crown
jewels** (leads/probate/WC/capstone). Then:
- **Utah = Sovereign's clean base + Ace's real features + the deeper free stack** —
  a working spine on day one instead of re-deriving it.
- **Sovereign (sold) = the rebrandable subset** of that shared base; **Utah
  (personal) = the superset** (adds the personal-only bits like F5-TTS, the full
  agentic harness, the revenue engines).

Net: don't treat Utah and Sovereign as competitors — **Sovereign's base is Utah's
Phase-0 starting point**, which is faster and lower-risk than the clean-room rebuild
the roadmap currently assumes.
