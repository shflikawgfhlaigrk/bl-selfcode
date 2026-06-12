"""Bus hardening: drop-on-overflow is counted and bounded, a subscriber that
closed its receive end without unsubscribing NEVER crashes the publisher (the
publish path feeds the deck — a crash there takes the daemon's event fabric
down), and the buffer floor guarantees at least one event is deliverable."""
from __future__ import annotations

import anyio

from utah.daemon.bus import Bus


def test_overflow_drops_and_counts_without_blocking():
    bus = Bus(buffer=2)
    sub = bus.subscribe()
    assert bus.publish("engine", {"n": 1}) == 1
    assert bus.publish("engine", {"n": 2}) == 1
    # buffer full → third event is dropped (never blocks the publisher)
    assert bus.publish("engine", {"n": 3}) == 0
    assert bus.dropped == 1
    assert bus.published == 3
    # the subscriber still gets the first two events, in order
    got = [sub.receive.receive_nowait()["event"]["n"] for _ in range(2)]
    assert got == [1, 2]
    bus._remove(sub.id)


def test_publish_to_dead_receiver_prunes_instead_of_crashing():
    """A subscriber whose receive stream closed (task crashed / connection died
    without __aexit__) must be pruned on the next publish — send_nowait raises
    BrokenResourceError there, and an uncaught one would crash every caller of
    publish (the ledger, the handlers, the deck feed)."""
    bus = Bus()
    sub = bus.subscribe(["engine"])
    sub.receive.close()  # receiver vanished without unsubscribing
    delivered = bus.publish("engine", {"n": 1})  # must not raise
    assert delivered == 0
    assert bus.subscribers == 0  # dead subscription was pruned
    # the bus keeps working for everyone else afterwards
    live = bus.subscribe(["engine"])
    assert bus.publish("engine", {"n": 2}) == 1
    assert live.receive.receive_nowait()["event"]["n"] == 2
    bus._remove(live.id)


def test_dead_receiver_does_not_starve_healthy_subscribers():
    bus = Bus()
    dead = bus.subscribe()
    healthy = bus.subscribe()
    dead.receive.close()
    assert bus.publish("risk", {"x": 9}) == 1  # healthy one still gets it
    assert healthy.receive.receive_nowait()["channel"] == "risk"
    bus._remove(healthy.id)
    assert dead.id not in bus._subs


def test_buffer_floor_is_at_least_one():
    """A zero/negative buffer would make EVERY publish a drop — the bus must
    clamp so a subscription is never born undeliverable."""
    bus = Bus(buffer=0)
    sub = bus.subscribe()
    assert bus.publish("engine", {"n": 1}) == 1
    assert sub.receive.receive_nowait()["event"]["n"] == 1
    bus._remove(sub.id)


def test_context_manager_unsubscribes():
    bus = Bus()

    async def go():
        async with bus.subscribe(["engine"]) as sub:
            assert bus.subscribers == 1
            assert sub.channels == {"engine"}
        assert bus.subscribers == 0

    anyio.run(go)


def test_remove_is_idempotent():
    bus = Bus()
    sub = bus.subscribe()
    bus._remove(sub.id)
    bus._remove(sub.id)  # second remove of the same id must be a no-op
    assert bus.subscribers == 0
