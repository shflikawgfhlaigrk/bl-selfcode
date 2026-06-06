# 18 — SPINE PROOF (Phase-0 / substrate items 2·3·4·5·6·8, live-proven)

> The provable spine: a **thin** orchestrator coordinating **fat, hardened**
> modules, wired to LIVE components (no injectable stand-ins), proven against a
> real running daemon. Built "massive" to absorb whatever autonomy adds later.
> Package: `utah/daemon/` (~965 LOC, 13 modules) + tests.

## What it realizes (one coherent spine = six audits)

| Audit | Realized by | Better than Ace |
|---|---|---|
| **2 processes** | `pool.py` bounded `CapacityLimiter` worker pool + `governor.py` live-load/in-flight admission gate + backpressure | Ace: ~120 procs, load 23.5, sync-on-loop 2–4 s lag. Utah: all blocking off-loop, loop answers in **0.4 ms** under load; heavy work shed at the door |
| **3 ipc** | `frame.py` 4-byte length-prefix + `read_exactly` + oversize-reject-before-alloc; `peercred.py` owner-only; `codec.py` zero-copy binary | Ace: 64 KiB readline cap, 6 mechanisms, dead cbor2 (silent-JSON). Utah: unbounded framed control + real fail-loud binary plane, one fabric |
| **4 pids** | `lifecycle.py` flock singleton (auto-release on crash, never deleted) + **verified hard-exit** (armed timer + `os._exit`) | Ace: zombie-daemon outage (`stop()` ≠ exited). Utah: process is *guaranteed* to terminate; liveness = probe, not pid-presence |
| **5 daemon** | `daemon.py` thin boot DAG → `run_forever` awaits stop; `dispatch.py` table + `handlers/` | Ace: 8,783-line god-file, 92 inline handlers. Utah: ~120-line core + a dispatch table; one bad handler can't crash the server |
| **6 objects** | `rpc.py` + `objects.py` msgspec Structs/enums | drift → type error, one model wire↔db |
| **8 binary** | `codec.py` packed-struct ticks/PCM via `numpy.frombuffer` (zero-copy), fail-loud | Ace's "fast path" was vapor; Utah's runs or raises |

## Live gate (against the real daemon, 2026-06-06) — 9/9

```
utah start → daemon ready (probe, not pid)      pid 97204
singleton lockfile records live pid             ✓
utah tell round-trips LIVE (real brain+pg)      [memory] "Michael lives in Gulf Shores, Alabama."
fabricated answer → "I don't know"              [brain] refuses (live, no fabrication)
agent runs in worker pool (off-loop)            pool.borrowed == 1
concurrent ping <50ms under load                0.4 ms
second daemon refuses (singleton flock)         rc=1 "already running"
zero ~/.ace access (lsof audit)                 0 refs
verified hard-exit (process really terminates)  shutdown ack {stopping:true} + exit 0
```

Tests: full suite green — 134 brain + 10 wire + 3 live-server (incl. the
no-loop-blocking assertion `ping < 50 ms` while `pool.borrowed == 1`).

## Wiring discipline (per Michael)
- **Live only, no injectable stand-ins at the wiring points:** the `tell` handler
  calls the real `utah.core.tell` (real Postgres + real Claude CLI); the proof is
  a live probe against a running daemon, not fake-backed tests.
- **No multi-wiring / overwrite:** one control socket, one dispatch table, one
  worker pool, one governor, one log stream. Nothing duplicated.
- **0 `~/.ace`:** isolation asserted at boot + lsof-audited live.

## Remaining on the spine
`supervisor.py` (always-on parent: probe liveness, bounded backoff + circuit-break,
reap, restart) + `cli.py` (`utah start|stop|status|ping|tell|restart`) + the
cross-process **event bus** (3-ipc push channel the dashboard's Sensor Array reads).
Then up the order: 1 programs · 10 storage · 11 interface · 12 product → stop at 13.
