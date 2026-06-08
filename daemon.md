# PART V — FOUNDATION · (A) THE DAEMON (spine), from the dirt

> Part V is Foundation: (A) daemon ← this · (B) storage (Postgres+pgvector) · (C)
> compounding memory. Same standard: dirt + web SOTA + sources + Utah design.

## V.A.0 Ground truth (dirt, daemon.py)
- **8,783 lines · 145 functions (73 async) · 92 inline IPC `_handle_*` · 17 module-
  level `run_*` cron jobs · 34 `run_in_executor`/`to_thread` band-aids · 11 raw
  blocking calls** (`time.sleep`/`requests`/`urlopen`/`.execute`) on/near the loop.
  Live: **3.4 GB RSS · 64 threads · 467 fds.**
- `start()` (line 1294) initializes **and owns in one process**: db, bus, metrics,
  scheduler, registry, world_model, ipc, llm/router, voice, trading supervisor,
  healer.
- Smells: **nested `asyncio.run()`** (lines 201/619/800), scattered `create_task`
  with **manual tracking (no TaskGroup/structured concurrency)**, a ~3,000-line
  voice loop inline.

## V.A.1 Failures
1. **Sync-I/O on the async loop → the live 2–4 s lag** (11 raw blockers; 34 ad-hoc
   offloads are a band-aid, not a boundary).
2. **God-file**: 92 inline handlers + inline voice loop + boot all in one file → a
   single choke point, effectively untestable.
3. **Nested `asyncio.run()`** — anti-pattern (only works because guarded; fragile).
4. **No structured concurrency** → orphan tasks, messy cancellation → feeds the
   zombie/verified-exit failure (§III.8).
5. **Monolith footprint** — one process holds everything (3.4 GB / 64 thr / 467 fds).

## V.A.2 Web SOTA (sourced)
- **Single event loop; never block it.** `time.sleep`/`requests`/sync DB in an
  `async def` is forbidden; blocking work goes to `loop.run_in_executor` (ThreadPool,
  or Python 3.14's **InterpreterPoolExecutor** for CPU-bound). Cross-thread →
  `run_coroutine_threadsafe`/`call_soon_threadsafe`. Bound external calls with
  semaphores/pools.
- **Structured concurrency:** **AnyIO task groups** (generalizing Trio nurseries)
  beat bare `asyncio.TaskGroup` (which can't list/cancel-all) → deterministic
  cancellation + graceful shutdown, no orphans.

## V.A.3 Utah daemon (design)
- **Thin orchestrator.** Boot = an explicit ordered DAG
  (`config → db → bus → scheduler → registry → ipc → listen`); `run_forever` just
  awaits the stop event. The daemon owns the **bus + subsystem handles** and
  nothing else inline.
- **Modular spine.** 92 inline `_handle_*` → an **IPC dispatch table + `handlers/`
  modules**; the voice loop, the cron jobs, and the world-model become their own
  modules (Parts III/IV).
- **AnyIO structured concurrency.** Every task lives in a task group → clean
  cancel + graceful shutdown → **kills the orphan/zombie chain** (§III.8).
- **Never block the loop.** ALL blocking work runs in a **bounded worker pool**
  (ThreadPool / 3.14 InterpreterPool); **agents run in the pool, not inline**; a
  lint rule bans sync I/O inside `async def`. → eliminates the live 2–4 s lag.
- **No nested `asyncio.run()`** — one loop; cross-thread via the threadsafe APIs.
- **Bounded concurrency** — per-external-service semaphores + the global resource
  governor (Part III).
- **Lifecycle** — SIGHUP reload, **verified hard-exit**, runs as a **child of the
  supervisor**, reaped (§III.8). Runtime = Python 3.14 (evaluate the free-threaded
  build for the worker pool); hot loops → Rust later only if measured (Part II).

## V.A.4 Quantified
| Dimension | Ace (measured) | Utah |
|---|---|---|
| `daemon.py` | 8,783 lines, 145 fns | thin core (~200) + focused modules |
| inline IPC handlers | 92 | 0 inline (dispatch table + `handlers/`) |
| sync-offload | 34 `run_in_executor` + 11 raw blockers | 0 sync on loop (bounded worker pool) |
| task lifecycle | scattered `create_task`, manual | **AnyIO task groups** (structured) |
| nested event loops | 3× `asyncio.run()` | none (one loop) |
| footprint | 3.4 GB · 64 thr · 467 fds | bounded, responsibilities split |
| event-loop lag | **2–4 s every ~5 min (live)** | **~0** (nothing blocks the loop) |

## V.A.5 Keep / Kill / Add
| | |
|---|---|
| **KEEP** | single resident async daemon owning expensive state · SIGHUP reload · the subsystem set (bus/scheduler/registry/memory/ipc) · launchd as OS entry |
| **KILL** | the 8,783-line god-file · 92 inline handlers · inline 3k-line voice loop · nested `asyncio.run()` · scattered `create_task` · **sync-I/O on the loop** |
| **ADD** | thin-orchestrator + modular spine · **AnyIO structured concurrency** · **bounded worker pool (no sync on loop)** · dispatch-table IPC · verified-exit under the supervisor |

Sources: asyncio dev docs https://docs.python.org/3/library/asyncio-dev.html · asyncio daemon task https://superfastpython.com/asyncio-daemon-task/ · AnyIO (why) https://anyio.readthedocs.io/en/stable/why.html · structured concurrency https://applifting.io/blog/python-structured-concurrency

---
