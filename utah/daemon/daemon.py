"""The daemon — a THIN orchestrator over fat, hardened modules.

Boot is an explicit ordered DAG (runtime → singleton → log → pool → governor →
dispatcher → control socket) and then it just awaits a stop signal. There are no
inline handlers, no sync I/O on the loop, no nested event loops — all of that
lives in the modules behind it. ``run_forever`` = wait for SIGTERM/SIGINT or the
``shutdown`` RPC, drain under a structured task group, then **verified hard
exit**. Everything is under ``~/.utah`` (isolation-checked at boot).
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import signal
import sys
import time

import anyio

from utah import __version__
from utah.daemon import lifecycle, runtime
from utah.daemon.bus import Bus
from utah.daemon.dispatch import Context, Dispatcher
from utah.daemon.governor import Governor
from utah.daemon.handlers import REGISTRY
from utah.daemon.pool import WorkerPool
from utah.daemon.data_server import DataServer
from utah.daemon.server import ControlServer

log = logging.getLogger("utah.daemon")

# -- deploy seams (env-overridable; bounded by design) -----------------------
POOL_LIMIT = int(os.environ.get("UTAH_POOL_LIMIT", "16"))
GOV_MAX_INFLIGHT = int(os.environ.get("UTAH_MAX_INFLIGHT", "64"))
# 1.5 = shed heavy work once the box is 50% oversubscribed (load1 > 1.5*ncpu).
# Was 8.0 (load1 > 144 on 18 cores) — it never tripped, so the load storm that
# killed AceOS could recur. ping/status bypass keeps the daemon answerable.
GOV_MAX_LOAD_PER_CORE = float(os.environ.get("UTAH_MAX_LOAD_PER_CORE", "1.5"))
DRAIN_TIMEOUT_S = float(os.environ.get("UTAH_DRAIN_TIMEOUT", "10.0"))
#: Grace after a stop is requested, so a `shutdown` RPC's ack flushes to the
#: caller before the socket is torn down (clean `utah stop`).
SHUTDOWN_GRACE_S = float(os.environ.get("UTAH_SHUTDOWN_GRACE", "0.2"))


def _setup_logging() -> None:
    runtime.ensure_runtime()
    handler = logging.handlers.RotatingFileHandler(
        runtime.LOG_PATH, maxBytes=8 * 1024 * 1024, backupCount=3
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger("utah")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.addHandler(logging.StreamHandler(sys.stderr))


async def amain() -> None:
    pool = WorkerPool(limit=POOL_LIMIT)
    governor = Governor(
        max_load_per_core=GOV_MAX_LOAD_PER_CORE, max_inflight=GOV_MAX_INFLIGHT
    )
    bus = Bus()
    shutdown = anyio.Event()
    ctx = Context(
        pool=pool,
        governor=governor,
        bus=bus,
        shutdown=shutdown,
        started_monotonic=time.monotonic(),
        version=__version__,
    )
    server = ControlServer(
        Dispatcher(ctx, REGISTRY), sock_path=runtime.CONTROL_SOCK, bus=bus
    )
    data_server = DataServer(sock_path=runtime.DATA_SOCK)  # doc-3/8 WIN binary plane

    # Configure the Postgres transition: apply the product-ledger schema and bind the
    # bus publisher so every revenue write (the schema Ace's producers transition into)
    # pushes to the deck. Deferred (not fatal) if Postgres is momentarily down — the
    # ledger reconnects lazily, same as memory.
    try:
        from utah.product import ledger as product_ledger

        product_ledger.get_ledger(publish=bus.publish).init_schema()
        log.info("product ledger schema ready (leads/probate/outreach/fires)")
    except Exception as exc:  # noqa: BLE001
        log.warning("product ledger schema init deferred: %s", exc)

    async with anyio.create_task_group() as tg:
        await tg.start(server.serve)
        await tg.start(data_server.serve)       # WIN binary plane on utahd-data.sock
        log.info(
            "utah daemon ready (pid %d, pool=%d) on %s (+data %s)",
            os.getpid(), POOL_LIMIT, runtime.CONTROL_SOCK, runtime.DATA_SOCK,
        )

        with anyio.open_signal_receiver(signal.SIGINT, signal.SIGTERM) as signals:
            async def _watch_signals() -> None:
                async for sig in signals:
                    log.info("signal %s → draining", getattr(sig, "name", sig))
                    shutdown.set()
                    return

            tg.start_soon(_watch_signals)
            await shutdown.wait()  # set by a signal OR the `shutdown` RPC
            await anyio.sleep(SHUTDOWN_GRACE_S)  # let a shutdown ack flush
            log.info("draining (verified-exit armed at %.1fs)…", DRAIN_TIMEOUT_S)
            lifecycle.arm_hard_exit(DRAIN_TIMEOUT_S)  # zombie-proof
            tg.cancel_scope.cancel()
    log.info("utah daemon drained cleanly")


def main() -> int:
    _setup_logging()
    runtime.assert_isolated(runtime.CONTROL_SOCK, runtime.LOCK_PATH, runtime.PID_PATH)
    try:
        lifecycle.Singleton().acquire()
    except lifecycle.AlreadyRunning as exc:
        print(exc, file=sys.stderr)
        return 1
    lifecycle.write_pidfile()
    try:
        anyio.run(amain)
    except KeyboardInterrupt:
        pass
    # Verified hard exit: the process really terminates (flock auto-releases).
    # The lockfile and pidfile are intentionally left (stale-safe; liveness is
    # a probe, never pid-presence).
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
