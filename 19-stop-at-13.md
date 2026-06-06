# 19 — STOP AT 13 (substrate 1–12 built + proven; gate before migration → autonomy)

> Per the build doctrine: build 1→12 in order, prove each against Ace, full live
> wiring / no injections / no multi-wiring, **STOP at 13 (autonomy)**, then 14
> migration (port Ace's tools to populate the dash), then autonomy on the proven
> base. This is that stop. Everything below is built and **live-proven** on this
> Mac, or is an honest blocker (never faked).

## Status of items 1–12

| # | Item | Built | Proof |
|---|---|---|---|
| 1 | programs | ✅ | kept stack locked (`requirements.lock`); 13G models; zero Ace dropped-deps; isolated |
| 2 | processes | ✅ | bounded pool + governor; ping **0.4 ms** under load |
| 3 | ipc | ✅ | control socket + len-prefix framing + peer-cred + **event bus** (cross-proc push) |
| 4 | pids | ✅ | flock singleton + verified hard-exit; SIGKILL→auto-restart |
| 5 | daemon | ✅ | thin orchestrator; dispatch table |
| 6 | objects | ✅ | msgspec Structs/enums |
| 7 | utah-spec | ✅ | synthesis (`7-UTAH-SPEC.md`), reconciled with the built spine |
| 8 | binary | ✅ | zero-copy ticks/PCM codec, fail-loud |
| 9 | rag (brain) | ✅ | 134/134 tests; beats gbrain + Ace (`17-brain-proof.md`) |
| 10 | storage | ✅ | Postgres MVCC 32 writers 0 locks (vs SQLite 29/32 lock-storm); DuckDB OLAP |
| 11 | interface | ✅ plumbing | event bus + web bridge: deck served + `/api/status` live + **SSE push** proven |
| 12 | product | ✅ ledger | revenue ledger, UNIQUE=never-twice, dashboard publish — 6/6 proven |

**Live gates passed:** spine 9/9, storage 3/3, bus pub/sub, web SSE push, ledger
6/6. Tests: **138 passed, 12 skipped** (skips = pg-integration without a throwaway
DSN). `utah start|stop|status|ping|tell|restart` real. Zero `~/.ace`, no injections.

## Honest blockers — what "fully done" needs that I can't fake

**A. Business inputs (item 12 live money flow) — Michael's call:**
1. **Resend sending domain + verified SPF/DKIM** — required before any real email.
2. **Real CAN-SPAM postal address** — required in every email footer.
3. **Posting credentials** (TikTok/IG) — required to post reels.
4. **WIN market feed access** + skin-in-the-game decision — before any trade.

The ledger, suppression (never-twice), enrichment hooks, and dashboard wiring are
built and proven; the gates open the moment these land.

**B. Native / large builds (item 11b voice) — buildable, not yet proven:**
5. **Apple SpeechAnalyzer STT** (native macOS 26) + openWakeWord + barge-in.
6. **Piper/F5 TTS** streaming (Piper models present; F5 deferred download).
7. **Per-panel SPA binding** — the deck currently fetches nothing; bind each of its
   ~40 panels to its live channel (the bus + SSE backend is proven; this is frontend).
8. **Agentic harness tool-streaming** — `tell` (Claude CLI) is the core; full
   shell/git/subagent tool surface + visible-thinking stream is the extension.

## Next (per doctrine, on the proven base)
**14 migration** — port Ace's tools (lead_scout, probate scrape, enrichment,
engine bridge, voice) onto this spine + ledger so they populate the deck. Then
**13 autonomy** — goal-gen → tiered self-coding → human-gated deploy — which is
simple precisely because 1–12 are proven. The dashboard schema map
(`dashboard/SCHEMA-MAP.md`) is the migration target.
