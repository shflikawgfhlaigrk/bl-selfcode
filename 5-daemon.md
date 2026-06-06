# 5 — DAEMON audit (the spine)

> The resident async process. Dirt (`acesd/core/daemon.py`) + web SOTA.

## 1. What we had
- **`daemon.py` = 8,783 lines, 145 functions (73 async)**, with **92 inline IPC
  `_handle_*`**, 17 module-level `run_*` cron jobs, **34 `run_in_executor`/`to_thread`
  band-aids, 11 raw blocking calls** near the loop, **nested `asyncio.run()`** (×3),
  scattered `create_task` (no structured concurrency), a ~3,000-line inline voice
  loop. Live: **3.4 GB · 64 threads · 467 fds.**
- `start()` owns *in one process*: db, bus, metrics, scheduler, registry,
  world_model, ipc, llm/router, voice, trading, healer.

## 2. Why we did it
A single resident daemon owning state over a unix socket is the right core model
(no cross-process cache coherence). It grew organically: each new capability got a
handler/loop added inline to the one file because that was fastest, and
`run_in_executor` was sprinkled wherever a blocking call was noticed.

## 3. What we didn't think about
- **Sync I/O on the loop → the live 2–4 s lag** (11 raw blockers; 34 ad-hoc
  offloads are a band-aid, not a boundary).
- **God-file:** 92 inline handlers + inline voice loop + boot = one untestable
  choke point; a subtle import/async error crashes everything.
- **Nested `asyncio.run()`** — fragile anti-pattern.
- **No structured concurrency** → orphan tasks, messy cancellation → feeds the
  zombie/verified-exit failure.
- **Monolith footprint** (3.4 GB / 64 thr / 467 fds).

## 4. What we're gonna change
- **Thin orchestrator:** boot = an explicit ordered DAG
  (`config→db→bus→scheduler→registry→ipc→listen`); `run_forever` just awaits stop.
- **Modular spine:** 92 inline handlers → **IPC dispatch table + `handlers/`
  modules**; voice loop, cron jobs, world-model = their own modules.
- **AnyIO structured concurrency** (task groups/nurseries) → deterministic cancel +
  graceful shutdown, no orphans.
- **Never block the loop:** ALL blocking work in a **bounded worker pool**
  (ThreadPool / 3.14 InterpreterPool); **agents run in the pool, not inline**; a
  lint rule bans sync I/O in `async def`.
- **No nested `asyncio.run()`** (one loop; threadsafe APIs cross-thread); per-service
  semaphores + the global governor; verified-exit child under the supervisor.

## 5. How it helps
| | Ace | Utah |
|---|---|---|
| daemon.py | 8,783 lines | thin core (~200) + modules |
| inline IPC handlers | 92 | 0 (dispatch table) |
| sync-on-loop | 34 offloads + 11 raw | 0 (worker pool) |
| task lifecycle | scattered, manual | AnyIO task groups |
| nested loops | 3× `asyncio.run()` | none |
| footprint | 3.4 GB · 64 thr · 467 fds | bounded, split |
| event-loop lag | **2–4 s / 5 min (live)** | **~0** |

Sources: asyncio dev docs https://docs.python.org/3/library/asyncio-dev.html · AnyIO (why) https://anyio.readthedocs.io/en/stable/why.html · structured concurrency https://applifting.io/blog/python-structured-concurrency
