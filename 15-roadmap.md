# 15 — START-TO-FINISH ROADMAP (with verify gates)

> Dirt → baseline → compound. Every phase ends with a **live-probe gate** (run in
> session, output recorded). **No phase starts until the prior gate is green.** This
> is the doctrine that fixes Ace's "compound on an unproven base." Build only begins
> after this spec is approved.

## Phase 0 — The provable spine (smallest thing that works)
**Build:** modular daemon (thin orchestrator + AnyIO) · control unix socket
(JSON-RPC, length-prefixed, peer-cred) · **Postgres+pgvector** (already verified) ·
msgspec object core · one capability end-to-end (`tell`) · supervisor + verified-exit.
**Gate:** `utah start`; `utah tell "…"` round-trips; an agent runs in the worker
pool while a concurrent `ping` stays <50 ms (no loop blocking); a fabricated answer
returns "I don't know"; everything under `~/.utah/`, **zero `~/.ace` access**
(audited). *Provisioning for P0 is already done — env, venv (101 pkgs), pg+pgvector
smoke, models copied.*

## Phase 1 — Memory that compounds
**Build:** grounded memory on pgvector (HNSW) + tsvector → **RRF hybrid (ON)** +
admission gate + provenance + decay + per-turn promotion; cross-encoder rerank stub;
entity graph wired into consolidation.
**Gate:** a fact stored this turn is recalled next turn; a live-data question skips
memory; hybrid fuses (BM25+dense); a backfill attempt is rejected at admission.

## Phase 2 — The agentic interface (the terminal replacement)
**Build:** unified voice+chat surface = the **frontier agentic harness** (Claude
SDK + shell/file/git/web/subagents + visible thinking); Apple SpeechAnalyzer STT +
Piper/F5 TTS + openWakeWord/smart-turn; intent-tiered (Apple FM/MLX fast → harness).
**Gate:** by voice, "audit your own stack" / "build feature X" runs with full
visible trace and does it; spoken reply <8 s warm; one web surface reflects backend
state via events (no stale poll).

## Phase 3 — The money pipeline firing
**Build:** port leads frontier + probate + outreach (→ Postgres ledger), enrichment,
**Resend + sending domain**; LTX-2 MLX video + posting pipeline; inbox triage.
**Gate:** a real compliant email reaches a real prospect (logged, unsubscribe works);
10 probate/day source-traceable; an approved reel posts; "any urgent mail" returns
real triage. *(Business inputs: postal address + sending domain.)*

## Phase 4 — Bounded autonomy (the multiplier)
**Build:** edge-triggered goal-gen (4 passes) → tiered self-coding A/B/C/D
(policy-as-data) → human-gated stash-verify-swap deploy; skill library; kill-switch
smoke test; provenance.
**Gate:** an induced failure yields exactly one human-gated PR (no branch storm);
deploy never resets the tree; kill-switch smoke refuses; provenance labels it.

## Phase 5 — Compounding capabilities (only on green)
Entity-recall, orchestration, marketing at scale, and — *only with skin in the
game* — one trading engine on the WIN feed with one real trade before a second.
**Gate:** the 14-day AGI bar (all conditions green) = "it compounds."

## Cutover & decommission
After Utah passes P0–P3 live: freeze Ace, harvest any last data through the
admission gate, then **boot out the 46 `com.ace.*` jobs and archive Ace**. Utah
becomes the only system.

## Standing rules (every phase)
Outcome scoreboard (works + makes money), not commits · one doc per substrate audit
+ this roadmap · capabilities not agents · nothing "done" without a live probe ·
entirely separate from Ace.
