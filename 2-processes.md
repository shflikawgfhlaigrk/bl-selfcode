# 2 — PROCESSES audit

> What actually runs and how it's supervised. Measured live 2026-06-06 + web SOTA.

## 1. What we had
- **18 cores / 64 GB**, load **23.54** — oversubscribed *right now*.
- **~120 resident processes**: 42 Python (5.7 GB RSS) + ~70 Chrome (4 profiles) +
  8 engines + lora-server + a bun/TS gbrain.
- **46 flat `com.ace.*` launchd jobs, no supervisor** — ~22 KeepAlive that never
  stop; the rest a mix of StartInterval / StartCalendarInterval crons.
- The daemon: 3.4 GB, 64 threads, 467 fds.
- ~20 TCP loopback ports (each engine = HTTP+WS on 2 ports) + 1 unix socket.
- 248 log files / 1.1 GB.

## 2. Why we did it
launchd is the native macOS supervisor, so "one plist per thing" was the path of
least resistance — every agent/loop/engine got its own KeepAlive job. Always-on
resident processes meant "it's always ready." Per-engine HTTP servers made each
independently pokeable. It scaled by *adding a job*, which felt like progress.

## 3. What we didn't think about
- **Oversubscription:** ~120 resident procs on 18 cores → **load 23.5** (the "load
  storm"); nothing budgets total concurrency.
- **Blind KeepAlive respawn:** 3 jobs crash-loop now (gbrain, engine_listener,
  wc_chrome); launchd's **exponential backoff can leave a crashed service down for
  hours**. 4 jobs were **OOM-killed** (rc=-9).
- **Event-loop blocking (LIVE):** the daemon blocks **2–4 s every ~5 min** —
  sync I/O on the async loop (IPC-flap root cause).
- **No supervision tree / ordering / resource governor** — 46 independent jobs.
- **Port + log sprawl** (~20 TCP ports, 248 logs); violates "unix socket not
  localhost."

## 4. What we're gonna change
- **One root supervisor**, not 46 flat jobs. It owns a *small* set of long-lived
  services + an **on-demand bounded worker pool**; everything else is
  **edge-triggered, bounded, single-instance — never KeepAlive.**
- **Bounded backoff + circuit-break** on crashes (stop respawning a chronic
  failure; alert once) — vs blind KeepAlive.
- **Global resource governor** — one admission gate caps concurrent heavy work by
  live load/memory.
- **Collapse processes:** one inference service (not daemon-MLX + lora-server + 8
  engines + bun gbrain); one managed browser (not 4 profiles); engines as
  in-process strategies or one host.
- **No sync I/O on the loop** (all blocking → worker pool); one structured rotated
  log; deploy = stash-verify-swap, **never `git reset --hard`**.

## 5. How it helps
| | Now | Utah |
|---|---|---|
| resident procs | ~120 | ~10 (**~12× fewer**) |
| launchd jobs | 46 | 1 supervisor |
| load | 23.5 (oversubscribed) | < cores |
| TCP ports | ~20 | ~1 |
| event-loop lag | 2–4 s / 5 min (live) | ~0 |
| crashes | blind respawn (3 looping) | backoff + circuit-break |
| logs | 248 files / 1.1 GB | 1 rotated stream |

Sources: launchd KeepAlive/throttle https://www.launchd.info/ · launchd.plist(5) https://www.manpagez.com/man/5/launchd.plist/ · s6 supervision https://skarnet.org/software/s6/
