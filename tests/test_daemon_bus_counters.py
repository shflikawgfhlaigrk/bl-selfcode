"""Bus observability + fan-out contracts: the published/dropped/pruned counters
tell the truth (the deck's Sensor Array reads them), fan-out delivers to every
matching subscriber independently, and the falsy-channels-means-ALL contract is
pinned (server._subscribe passes ``params.get("channels")`` straight through —
``[]``/``None`` both mean the firehose)."""
from __future__ import annotations

import anyio
import pytest

from utah.daemon.bus import Bus


def test_fan_out_delivers_to_every_matching_subscriber():
    bus = Bus()
    on_engine_1 = bus.subscribe(["engine"])
    on_engine_2 = bus.subscribe(["engine"])
    on_risk = bus.subscribe(["risk"])

    assert bus.publish("engine", {"n": 1}) == 2  # both engine subs, not risk
    assert on_engine_1.receive.receive_nowait()["event"]["n"] == 1
    assert on_engine_2.receive.receive_nowait()["event"]["n"] == 1
    with pytest.raises(anyio.WouldBlock):
        on_risk.receive.receive_nowait()
    for sub in (on_engine_1, on_engine_2, on_risk):
        bus._remove(sub.id)


def test_publish_with_no_subscribers_returns_zero_and_still_counts():
    bus = Bus()
    assert bus.publish("engine", {"n": 1}) == 0
    assert bus.published == 1  # the counter is publishes, not deliveries
    assert bus.dropped == 0 and bus.pruned == 0


def test_pruned_counter_increments_once_per_dead_subscriber():
    bus = Bus()
    dead = bus.subscribe(["engine"])
    dead.receive.close()
    bus.publish("engine", {"n": 1})
    assert bus.pruned == 1
    bus.publish("engine", {"n": 2})  # already pruned: no double count
    assert bus.pruned == 1


def test_empty_channel_list_means_all_channels():
    """Pinned contract: the server hands ``params.get("channels")`` straight to
    ``subscribe`` — a client sending ``[]`` (or omitting the key) gets the
    firehose, exactly like ``None``. Changing this silently breaks the deck."""
    bus = Bus()
    sub = bus.subscribe([])
    assert sub.channels is None
    assert bus.publish("engine", {"n": 1}) == 1
    assert bus.publish("risk", {"n": 2}) == 1
    got = [sub.receive.receive_nowait()["channel"] for _ in range(2)]
    assert got == ["engine", "risk"]
    bus._remove(sub.id)


def test_duplicate_channels_collapse_to_one_delivery_each():
    bus = Bus()
    sub = bus.subscribe(["engine", "engine"])
    assert sub.channels == {"engine"}
    assert bus.publish("engine", {"n": 1}) == 1  # one delivery, not two
    assert sub.receive.receive_nowait()["event"]["n"] == 1
    bus._remove(sub.id)


def test_unsubscribe_ends_the_receive_stream_for_the_consumer():
    """_remove closes the send end, so a consumer loop iterating ``receive``
    terminates instead of blocking forever on a subscription that no longer
    exists (the server's pump task must exit, not leak)."""
    bus = Bus()
    sub = bus.subscribe(["engine"])
    bus._remove(sub.id)
    with pytest.raises(anyio.EndOfStream):
        sub.receive.receive_nowait()


def test_drop_counter_is_per_subscriber_not_global_stall():
    """One full subscriber drops; a healthy one on the same channel still gets
    every event — slow consumers never degrade their neighbors."""
    bus = Bus(buffer=1)
    slow = bus.subscribe(["engine"])
    fast = bus.subscribe(["engine"])
    assert bus.publish("engine", {"n": 1}) == 2
    # slow never drains; fast does
    assert fast.receive.receive_nowait()["event"]["n"] == 1
    assert bus.publish("engine", {"n": 2}) == 1  # slow full → dropped, fast served
    assert bus.dropped == 1
    assert fast.receive.receive_nowait()["event"]["n"] == 2
    for sub in (slow, fast):
        bus._remove(sub.id)
