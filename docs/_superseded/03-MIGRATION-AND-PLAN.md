# 03 — Migration, wiring, processes, and the plan

How we get from "$0 on a rotten base" to "everything Ace was supposed to be," on
a clean tree, with verify gates between every step.

---

## 1. Keep / Migrate / Change / Discard manifest

Consolidated from the six subsystem audits. "Migrate" = port the *source module*
into Utah's tree and re-prove it; "Change" = port the idea, rewrite the code;
"Discard" = leave in Ace's archive, do not carry.

### KEEP / PORT-CLEAN (proven, valuable, low-rot)
- **Lead frontier** (`osm_metro_frontier.py` + registry) — real 500 SMBs/day, a
  moving 72-metro frontier with dedup. Port the rotation logic; add email
  enrichment as the missing lever.
- **Probate scraper** (`probate.py` + `gpn_feed.py`) — real GA probate notices,
  statewide. Port; add heir skip-trace as the missing lever.
- **Outreach sender** (`lead_outreach_send.py` stack) — suppression + sent-ledger
  + unsubscribe + ramp, one real send proven. Port the pipeline; move ledger to
  **Postgres** (transactional "never twice"); replace the placeholder gate with a
  real decision (address + sending domain).
- **WealthCharts feed bridge** (`wc_cdp_bridge.py` + watchdog) — hard-won market
  data integration. Port as the reference feed *producer into WIN*; off by
  default (trading is out-of-scope day 1).
- **Voice/MLX tuning** — L1 fast path, warm-start, Metal cache cap, thermal
  ratchet, pre-roll buffer, Piper TTS stripping (see `02` §4).
- **Capstone MCP gateway** — one MCP over 130+ servers, `brain_search`+`remember`
  closed loop, live Gmail send. Re-host under `~/.utah/`; it's the operator's
  hands.
- **Gmail + Keychain OAuth bridge**, **Pushover alerts**, **Browser/Playwright
  agent**, **Claude CLI tier** — all live, simple, port-as-is.
- **Contracts** — Agent base, MCP read/write protocol, IPC schema model.
- **Spine patterns** — atomic PID, SIGHUP reload, module-level scheduler job fns,
  the event bus, the verified-exit shutdown lesson.
- **Memory guards** — the layered no-fabrication contract (ported + cleaned).
- **Crystals** (content-addressed goal tracking) and the **decay model**.

### CHANGE / REWRITE-FRESH (idea good, code wrong)
- **The daemon spine** — break the 8,783-line god-file into the modular layout in
  `01` §4. Same responsibilities, message-passing instead of state-capture.
- **IPC dispatch** — 60 inline handlers → a dispatch table + `handlers/` modules.
- **Memory-first guard** — Ace's 5 hardcoded chat-regex filters → a generic,
  caller-agnostic `LiveDataGuard` + Sovereign-style `overlap_score`.
- **Grounding/no-fabrication** — make it a *brain-contract* rule, not per-agent
  hardcoding.
- **Autonomy loops** — KeepAlive → **edge-triggered, worktree-reused,
  human-gated** (see §3). This is the churn fix.
- **Marketing reels** — keep the renderer, **build the missing posting pipeline**
  (it never existed).
- **Complexity classifier**, **model loaders** — retrain/unify (see `02` §4).
- **Frontend** — replace the SwiftUI HQ + 7,084-line `hq_http.py` with **one
  web dashboard** (event-driven, §2).

### DISCARD (sunk cost — archive Ace, carry nothing)
- **All 8 trading engines** (~30K LOC, $0, no edge, never executed). Archive
  `~/debt/` read-only for reference; port *patterns* only if trading ever returns
  with skin in the game.
- **The prediction loop** (post-hoc rationalization, no alpha).
- **The 4,758-branch autonomous churn machinery** in its KeepAlive form.
- **The second frontend** (SwiftUI HQ) and the contract codegen coupling.
- **gbrain as a second daemon** (fold into the one memory service, `02` §3).
- **Tailscale phone *proxy*** as a process (the web frontend + Tailscale Serve
  covers phone access; keep Serve, drop the bespoke proxy).
- **Scattered config files**, **stale `*PLAN*/*DUMP*/HANDOFF*` docs**, **~2.3 GB
  of `~/.ace/_*_bak*` debris** — none of it crosses into `~/.utah/`.

---

## 2. Backend ↔ frontend wiring (the rule that kills "backend produces, surface
doesn't reflect")

Ace's gap: backend computed state that no `/api/*` consumer surfaced, and a
second Swift frontend was coupled to the contract via codegen, and panels polled
and went stale. Utah's wiring is **one contract, event-pushed, one frontend.**

```
   web dashboard (browser / phone via Tailscale Serve)
        │     ▲
   HTTP │     │ WebSocket (control events as JSON; numeric as binary WIN frames)
        ▼     │
   com.utah.web  (FastAPI thin client — owns NO facts, NO reasoning)
        │     ▲
  JSON-RPC     │ event stream
        ▼     │
   com.utah.daemon  ── bus ──►  every state change EMITS an event
        │
   ~/.utah/utah.sock (control)   +   ~/.utah/win.sock (data) ──► live charts
```

The binding wiring rules:
1. **The daemon is the only source of truth.** The frontend renders daemon state;
   it never computes or caches facts. (Sovereign's "thin client" model.)
2. **If the backend produces it, an event carries it to the frontend
   automatically.** No hand-wired panel that can silently go stale. A new backend
   fact = a new bus event = it appears. The "surface gap" becomes structurally
   impossible.
3. **One frontend, web.** Portable, phone-ready over Tailscale Serve, no codegen
   coupling. The IPC method/event catalogue is the *single* contract both sides
   read.
4. **Control over JSON-RPC, live numeric over WIN.** Charts/telemetry stream
   binary; buttons/forms speak JSON. No ticks through JSON.
5. **Backend-before-frontend is banned as a stopping point.** A feature isn't
   "done" until its state is visible in the dashboard via an event — that's part
   of the phase's verify gate.

---

## 3. Processes & subprocesses to configure (launchd, autonomy, deploy)

**launchd (`com.utah.*`, all bootstrap at login, KeepAlive only where a *resident
service* — never for an autonomy loop):**
- `com.utah.daemon` — resident, KeepAlive. The brain.
- `com.utah.win` — resident, KeepAlive. The data plane.
- `com.utah.web` — resident, KeepAlive. The frontend host.
- `com.utah.workers` — resident pool, KeepAlive. Runs agents off the loop.
- `com.utah.scheduler` — edge/cron triggers (or in-daemon).
- `com.utah.feed` — only if trading returns; off by default.

**Autonomy (the churn fix — these are NOT KeepAlive):**
- Triggered by **edges** (a git push to `main`, a failed agent event, a new
  proposal), not a timer storm.
- **Single-instance** (flock), **bounded** (per-run timeout + token budget cap),
  **worktree-reused** (one persistent worktree rebased, not a branch per cycle →
  branch spam drops ~10:1), **provenance-stamped** (every autonomous commit
  carries a run-id trailer + a distinct author, so "did a human touch this?" is
  always answerable).
- **Deploy is human-gated by default.** Self-coding opens a PR; it merges only
  after (a) the gate is green on the PR branch, (b) you've seen the plan, (c) a
  veto window passes. **Never `git reset --hard` a live tree** — stash-first,
  verify, reload; if verify fails, restore the stash, never discard work.

**Subprocesses the daemon owns (bounded, off-loop):**
- Claude CLI escalation (`claude -p`, budget + timeout capped).
- Agent runs (in `workers`, `asyncio.wait_for` + `run_in_executor`).
- Browser/Playwright sessions.
- Local MLX model processes / whisper.cpp.

---

## 4. The phased roadmap (everything Ace was supposed to be, gated)

Each phase ends with a **Verify gate**: a live probe, run in-session, output
recorded. **No phase starts until the prior gate is green.** This is the machine
enforcing the doctrine.

### Phase 0 — Spine (the base that compounds)
Modular daemon, two sockets (control + WIN), bounded workers, typed config,
event bus, one web frontend skeleton, grounded memory with the admission/recall
guards. Total isolation from Ace verified.
**Gate:** `utah start` runs; `utah tell "…"` round-trips; an agent runs in the
worker pool while a concurrent `ping` stays <50 ms (proves no loop blocking); a
fabricated-answer attempt returns "I don't know"; a WIN tick window renders on
the dashboard. All `~/.utah/`, zero `~/.ace/` access (audited).

### Phase 1 — The money pipeline, actually firing
Port lead frontier + probate + outreach. Move the sent-ledger to Postgres. Add
email enrichment (skip-trace) and a real sending domain (Resend + SPF/DKIM).
Replace the placeholder gate with a real address.
**Gate:** a real, CAN-SPAM-compliant email goes to a real prospect through the
ramped sender, logged in the Postgres ledger, with a working unsubscribe — and
the dashboard shows it. This is the first dollar's plumbing, proven end-to-end.

### Phase 2 — Voice + memory + the operator surface
Port the MLX/voice stack (L1 fast path, Piper, thermal, pre-roll). Wire the
capstone MCP. The web dashboard shows live agent status, memory, leads, alerts —
all event-pushed.
**Gate:** "Hey Utah, …" → spoken grounded reply <8 s warm, no fabrication; the
dashboard reflects a backend state change with no manual wiring.

### Phase 3 — Bounded autonomy (the thing that "runs forever")
Edge-triggered self-improvement: a failed agent or a code push triggers one
bounded, worktree-reused, provenance-stamped run that opens a human-gated PR.
**Gate:** an induced failure produces exactly one PR (not a branch storm), the
deploy path stashes-verifies-reloads without ever resetting the tree, and the
provenance tool correctly labels the commit autonomous.

### Phase 4+ — Compounding capabilities (only on a green base)
Entity-graph-boosted recall, parallel agent orchestration, marketing-reel
*posting*, and — *only if you choose it, with skin in the game* — a single
trading engine on the WIN feed with one real trade before any second engine.

---

## 5. "What Ace was supposed to be" → where it lands

| Ace's promise | Status on Ace | Where Utah delivers it |
|---|---|---|
| Always-on local voice AI that works | fragile, flappy | Phase 0+2 (bounded spine + ported voice) |
| Grounded memory, no hallucination | patched reactively | Phase 0 (layered guard, enforced) |
| Makes money (leads → outreach) | built, gated, $0 | Phase 1 (ledger→Postgres, enrich, domain, real send) |
| Runs forever without babysitting | churn spiral | Phase 3 (edge-triggered, human-gated) |
| One operator surface | two frontends, stale panels | Phase 2 (one web frontend, event-pushed) |
| Fast | JSON on hot paths | Phase 0 (WIN data plane + Rust hot paths) |
| Self-improving | 4,758 dark branches | Phase 3 (bounded, provenance, gated deploy) |
| Trading edge | $0, no edge | Phase 4, optional, skin-in-the-game-first |

The throughline: **Ace had almost all the right *ideas* and built them on a base
that could never let them compound. Utah keeps the ideas and the proven parts,
puts them on a base that's verified at every step, and refuses to call anything
done until a live probe says it works for you.**
