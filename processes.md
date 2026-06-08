# PART III — PROCESSES (from the dirt)

> What actually runs, how it's supervised, how the pieces talk, how it fails, and
> what Utah does instead. Measured live on 2026-06-06. Planning only.

## III.0 Ground truth (measured live, right now)

```
Hardware ............ 18 logical cores (6 perf + 12 eff), 64 GB RAM
Load average ........ 23.54  → OVERSUBSCRIBED (load > cores, on a laptop)
launchd jobs ........ 46 com.ace.* loaded (49 plists), ~22 KeepAlive-resident
Python procs ........ 42  (5.7 GB RSS total)
Chrome procs ........ ~70 across 4 profiles (chrome-ace 34, hq 11, wc 7, debug 3)
Engines ............. 8 (shadow/ctx_alpha/ctx_bravo/perp/research/bible/barber/antigrav)
daemon (pid live) ... 3.4 GB RSS, 64 threads, 467 open fds
IPC surface ......... ~20 TCP loopback ports + exactly 1 unix socket (ace.sock)
Logs ................ 248 files, 1.1 GB in ~/.ace/logs
Live failures NOW ... 3 crash-looping (brain/gbrain[bun], engine_listener, wc_chrome),
                      4 OOM-killed (rc=-9: agent_quality_reviewer, consolidate_memory,
                      dream, safety_smoke), event-loop lag 2–4 s every ~5 min
```

The headline: **~120 resident processes on 18 cores at load 23.5.** This is the
"load storm" — not a bug to fix, the *architecture* producing it.

## III.1 The process inventory (what runs, how it's supervised)

| Class | Members | Supervision | Note |
|---|---|---|---|
| **Core resident (KeepAlive)** | daemon (`acesd.core.daemon`), hq_http (via `ace_guard.py`), apex_prime | KeepAlive=true, throttle 10s | daemon = 3.4 GB monolith |
| **Engines (KeepAlive)** | 8 engine procs + engine_listener | KeepAlive=true | each = own HTTP+WS on 2 TCP ports |
| **Inference (KeepAlive)** | lora-server (`mlx_lm.server` Llama-3.2-3B :8088) | KeepAlive | a 2nd model-holder beside the daemon's MLX |
| **Brain (KeepAlive, crashing)** | `brain` = `bun cli.ts serve` :3131 (gbrain) | KeepAlive, **Crashed=true** | a whole **TypeScript/Bun** runtime, crash-looping |
| **Browser (KeepAlive)** | wc_chrome (+ 3 more profiles), wc_scraper_watchdog | KeepAlive, wc_chrome **Crashed=true** | ~70 Chrome procs total |
| **Autonomy loops** | optimize_loop (KeepAlive!), revenue-loop (KeepAlive), live-research, fleet_grader, phone_proxy | KeepAlive | always-on loops, not edge-triggered |
| **Autonomy (booted-out)** | redeploy (SI 3600), auto-pr-merger (SI 600), autonomous-heartbeat (SI 10800) | plists present, **not loaded** (the git-reset hazard) | disabled by hand |
| **Cron (StartInterval)** | claude_overseer 300s, news_ingest 600s, browse-optimize 1500s, code_watcher 3600s, lite_triage 3600s, log_trim 300s, trade-ledger 900s, train-autopilot 270s | independent timers | each its own throttle |
| **Calendar (daily)** | lead_scout_daily, lead-enrich, lead-send, smb_contacts_daily, morning-brief, marketing-reels, revenue-watchdog, consolidate_memory, dream, engine-autotune, overperf, safety_smoke, trainer-cycle, agent_quality_reviewer | StartCalendarInterval | the daily revenue/maintenance jobs |

**There is no supervisor.** 46 flat, independent launchd jobs with four different
trigger styles and per-job throttles. launchd just respawns each blindly.

## III.2 Process-level failures (evidenced from the dirt)

1. **Oversubscription / the load storm (LIVE).** ~120 resident processes on 18
   cores → load **23.54**. ~22 KeepAlive jobs never stop; nothing budgets total
   concurrency. A laptop running a small data center.
2. **Event-loop blocking (LIVE, logged minutes ago).** `ace.loop_monitor` records
   the loop blocked **2–4 s every ~5 min** — "a coroutine made a synchronous
   blocking call on the loop thread." This is the IPC-flap root cause, still
   happening. The daemon's **64 threads** are the `run_in_executor` band-aid, not
   a cure.
3. **Crash-loop respawn churn.** `brain`(bun), `engine_listener`, `wc_chrome` are
   KeepAlive with `Crashed=true` — launchd respawns them **forever, under load**,
   each restart adding to the storm. Blind respawn ≠ recovery.
4. **OOM kills (rc=-9 × 4).** agent_quality_reviewer, consolidate_memory, dream,
   safety_smoke were SIGKILL'd — memory pressure from the resident fleet.
5. **Port sprawl over TCP loopback.** ~20 listening TCP ports (every engine =
   HTTP+WS on 2 ports, + apex 8500, lora 8088, fleet 8550, gbrain 3131, hq 8765,
   ws 9101). Only **one** unix socket exists — so the "unix socket not localhost"
   principle is violated in practice; the real fabric is a tangle of HTTP servers.
6. **Multiple model-holders.** daemon (MLX tiers) + lora-server (Llama-3B) + 8
   engines + bun gbrain each hold memory → the 161 GB OOM lineage and today's
   rc=-9 kills.
7. **No supervision tree, no resource governor, no ordering.** 46 jobs, no
   dependency graph, no global admission control, no backoff. A crash-looper and
   a daily cron and an always-on loop are all "just launchd jobs."
8. **Heavy monolith daemon.** 3.4 GB, 64 threads, **467 fds** (approaching limits;
   the Errno-24 lineage). One process doing too much.
9. **Deploy that resets the live tree.** `redeploy` did `git reset --hard` and
   wiped WIP → had to be booted out. Process management that destroys work.
10. **Logging sprawl.** 248 log files, 1.1 GB — per-process logs, no single
    structured stream, hard to see the system as one thing.
11. **Browser sprawl.** 4 Chrome profiles, ~70 processes, one crash-looping.

## III.3 What Utah does instead (researched) + why

1. **ONE supervised process tree, not 46 flat jobs.** launchd starts exactly
   **one** root supervisor. The supervisor owns a *small* set of long-lived
   services and an **on-demand, bounded worker pool**. Everything else is
   **edge-triggered, bounded, single-instance — never KeepAlive.** *Why:* a
   supervision tree (OTP/`s6`/`runit` model) gives ordered start, one place to
   reason about liveness, and **bounded restart intensity** — vs launchd's blind
   per-job respawn that turns a crash into a storm.
2. **Bounded backoff + circuit-break on crashes.** A child that keeps dying backs
   off exponentially and is **circuit-broken** (stop respawning, alert once) — the
   opposite of KeepAlive hammering `brain`/`wc_chrome` forever. *Why:* respawn
   churn is itself load; a chronically-failing service should go dark loudly, not
   thrash.
3. **A global resource governor.** One admission gate caps concurrent heavy work
   by live load/memory before spawning. *Why:* this is the structural fix for load
   23.5 — no component can oversubscribe the box; the loops' ad-hoc load-guards
   become one enforced policy.
4. **One IPC fabric, two sockets.** Unix **control** socket (JSON-RPC) + unix
   **WIN data** socket (binary) — **kill the ~20 TCP loopback ports.** Engines and
   services speak the bus; they are not each an HTTP server. Only the *one*
   deliberately-exposed surface (the dashboard / phone via Tailscale) binds a
   port. *Why:* restores "unix socket not localhost," collapses the wiring, and
   removes a tangle of TCP listeners.
5. **Collapse the model-holders to ONE inference service.** daemon-MLX +
   lora-server + per-engine models + bun gbrain → a **single inference service**
   other processes call. Engines become **in-process strategies or one engine
   host**, not 8 daemons. gbrain folds into the one memory service (no bun). *Why:*
   one model cache, one memory ceiling — kills the OOM lineage.
6. **No sync I/O on the loop, ever.** All blocking work runs in the bounded worker
   pool; the loop only awaits. *Why:* directly eliminates the live 2–4 s lag and
   the IPC flaps — the band-aid 64 threads become a real boundary.
7. **One managed browser, not 4 profiles × 70 procs.** A single pooled,
   health-checked browser context. *Why:* ~70 Chrome procs is most of the load.
8. **Deploy never touches the live tree destructively.** Stash-verify-swap, never
   `git reset --hard`. *Why:* process management must not delete work.
9. **One structured log stream** (rotated), not 248 files. *Why:* see the system
   as one thing; bound disk.

## III.4 How much better — quantified

> Current numbers measured live today; targets are the design goals to verify at
> the build gate (no fabrication).

| Dimension | Now (measured) | Utah target | Delta |
|---|---|---|---|
| Resident processes | ~120 (42 py + ~70 chrome + 8 eng) | ~10 (1 supervisor + ~5 services + workers + 1 browser) | **~12× fewer** |
| launchd jobs | 46 | 1 root supervisor (+ a few OS hooks) | **46 → 1** |
| Load average | **23.54** on 18 cores (oversubscribed) | < 18 under normal op | **back under cores** |
| Listening TCP ports | ~20 loopback | ~1 (dashboard) + 2 unix sockets | **~20 → ~1** |
| Event-loop lag | 2–4 s every ~5 min (live) | 0 (no sync on loop) | **eliminated** |
| Crash handling | blind KeepAlive respawn (3 looping now) | bounded backoff + circuit-break | **storm → contained** |
| Model-holder processes | 4+ (daemon/lora/engines/gbrain) | 1 inference service | **one model cache** |
| daemon footprint | 3.4 GB, 64 thr, 467 fds | bounded + split | **no 467-fd monolith** |
| Logs | 248 files, 1.1 GB | 1 rotated stream | **single pane** |
| Deploy safety | `git reset --hard` live tree (booted out) | stash-verify-swap | **no work loss** |

**Net:** ~120 resident processes → ~10; 46 launchd jobs → one supervisor; load
back under core count; ~20 TCP ports → ~1; the live event-loop lag gone; crash
storms contained; one model cache instead of four. The current numbers are facts
as of today; the targets are the gate to verify against.

## III.5 Process-layer carry-over (Keep / Kill / Add)

| | Items |
|---|---|
| **KEEP** | launchd as the single OS entry (starting ONE root), the unix control socket, the load-guard *idea* (promoted to a global governor), `ace_guard`'s crash-cement *idea* (promoted to a supervisor) |
| **KILL** | 46 flat jobs, ~22 KeepAlive residents, ~20 TCP loopback ports, lora-server as a separate process, **bun/gbrain** as a 2nd brain/runtime, 4 Chrome profiles, the `git reset --hard` redeploy, 248-file log sprawl, the 8 separate engine daemons |
| **ADD** | one root **supervisor** (supervision tree, bounded restart), a **global resource governor**, the **WIN data socket**, **backoff + circuit-break**, one **inference service**, one **managed browser pool**, one **structured rotated log** |


---
