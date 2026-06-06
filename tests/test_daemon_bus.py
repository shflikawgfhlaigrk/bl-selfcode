"""Cross-connection pub/sub over the live control socket: one connection
subscribes, another publishes, the event is pushed (no polling)."""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import anyio

from utah.daemon import client as ctl
from utah.daemon.bus import Bus
from utah.daemon.dispatch import Context, Dispatcher
from utah.daemon.governor import Governor
from utah.daemon.handlers import REGISTRY
from utah.daemon.pool import WorkerPool
from utah.daemon.server import ControlServer


def test_publish_is_pushed_to_subscriber():
    d = tempfile.mkdtemp(prefix="ut", dir="/tmp")
    sock = os.path.join(d, "d.sock")
    pool = WorkerPool(limit=2)
    gov = Governor(max_load_per_core=1e9, max_inflight=1000)
    bus = Bus()
    ctx = Context(pool=pool, governor=gov, bus=bus, shutdown=anyio.Event(),
                  started_monotonic=time.monotonic(), version="test")
    server = ControlServer(Dispatcher(ctx, REGISTRY), sock_path=Path(sock), bus=bus)
    received: list = []

    async def scenario():
        async with anyio.create_task_group() as tg:
            await tg.start(server.serve)

            async def sub():
                async for ev in ctl.subscribe(["engine"], sock_path=sock):
                    received.append(ev)
                    return  # first event → done

            tg.start_soon(sub)
            await anyio.sleep(0.25)  # ensure the subscription is live
            delivered = await ctl.call(
                "publish", {"channel": "engine", "event": {"fired": "antigrav", "pnl": 12.5}},
                sock_path=sock,
            )
            received.append(("delivered", delivered["delivered"]))
            await anyio.sleep(0.3)
            tg.cancel_scope.cancel()

    anyio.run(scenario)
    try:
        os.unlink(sock); os.rmdir(d)
    except OSError:
        pass

    delivered = next(r[1] for r in received if isinstance(r, tuple))
    events = [r for r in received if isinstance(r, dict)]
    assert delivered == 1
    assert events and events[0]["channel"] == "engine"
    assert events[0]["event"]["fired"] == "antigrav" and events[0]["event"]["pnl"] == 12.5


def test_channel_filter_excludes_other_channels():
    bus = Bus()
    sub = bus.subscribe(["risk"])
    assert bus.publish("engine", {"x": 1}) == 0  # not subscribed to 'engine'
    assert bus.publish("risk", {"x": 2}) == 1
    bus._remove(sub.id)
