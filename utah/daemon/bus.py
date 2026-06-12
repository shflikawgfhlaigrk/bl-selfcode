"""In-daemon pub/sub event bus — the cross-process push channel.

This is 3-ipc's broker: programs ``publish`` events to the daemon and
subscribers (the dashboard's "Sensor Array", agents, the CLI) receive them
**pushed** over the same control socket — no polling, no file-as-channel, no 20
TCP ports. Each subscriber has a bounded buffer with **drop-on-overflow** so a
slow subscriber (a stalled dashboard tab) can never back-pressure a publisher
(the engine/tick feed). One fabric, one direction: backend produces → surface
reflects.
"""
from __future__ import annotations

import itertools
import logging

import anyio

log = logging.getLogger("utah.daemon.bus")


class Subscription:
    """A live subscription. Use as an async context manager; iterate ``receive``."""

    def __init__(self, sid: int, channels: set[str] | None, send, receive, bus: "Bus") -> None:
        self.id = sid
        self.channels = channels
        self._send = send
        self.receive = receive
        self._bus = bus

    async def __aenter__(self) -> "Subscription":
        return self

    async def __aexit__(self, *exc) -> None:
        self._bus._remove(self.id)


class Bus:
    """Process-local fan-out. ``channels=None`` on a subscription means 'all'."""

    def __init__(self, buffer: int = 256) -> None:
        # Floor of 1: a zero/negative buffer would make every subscription
        # born-undeliverable (every publish an instant drop).
        self._buffer = max(1, int(buffer))
        self._subs: dict[int, tuple] = {}
        self._ids = itertools.count(1)
        self.published = 0
        self.dropped = 0
        self.pruned = 0

    def subscribe(self, channels=None) -> Subscription:
        """Register a subscriber. A *falsy* ``channels`` (``None`` OR ``[]``)
        means ALL channels — the server hands ``params.get("channels")``
        straight through, so an omitted key and an empty list are both the
        firehose. Pinned by test; changing it silently breaks the deck."""
        sid = next(self._ids)
        send, receive = anyio.create_memory_object_stream(self._buffer)
        chans = set(channels) if channels else None
        self._subs[sid] = (send, chans)
        return Subscription(sid, chans, send, receive, self)

    def _remove(self, sid: int) -> None:
        pair = self._subs.pop(sid, None)
        if pair is not None:
            pair[0].close()

    def publish(self, channel: str, event: dict) -> int:
        """Fan out one event. Never blocks and never raises on a bad subscriber:
        a full buffer drops the event (counted), a dead receive end (subscriber
        task died without unsubscribing) prunes the subscription — an uncaught
        BrokenResourceError here would crash every publisher (ledger, handlers,
        the deck feed)."""
        msg = {"channel": channel, "event": event}
        delivered = 0
        for sid, (send, chans) in list(self._subs.items()):
            if chans is not None and channel not in chans:
                continue
            try:
                send.send_nowait(msg)
                delivered += 1
            except anyio.WouldBlock:
                self.dropped += 1
                log.debug("subscriber %d full → dropped event on %s", sid, channel)
            except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                self._remove(sid)
                self.pruned += 1
                log.debug("subscriber %d dead → pruned on publish to %s", sid, channel)
        self.published += 1
        return delivered

    @property
    def subscribers(self) -> int:
        return len(self._subs)
