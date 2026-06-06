"""Bounded worker pool — the rule that keeps the event loop free.

Ace blocked its async loop with sync I/O (2–4 s lag every ~5 min). Utah's law:
**no blocking work on the loop, ever.** Every heavy/blocking call (a brain turn,
a Postgres query, an agent) runs here, off-loop, under an ``anyio.CapacityLimiter``
so a burst can never spawn unbounded threads or starve the loop. While the pool
is saturated, the loop still answers ``ping`` in microseconds — that is the
Phase-0 no-loop-blocking gate.
"""
from __future__ import annotations

import anyio


class WorkerPool:
    """A capacity-limited offload boundary for blocking work."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError(f"worker pool limit must be >= 1, got {limit}")
        self._limit = limit
        self._limiter = anyio.CapacityLimiter(limit)

    async def run(self, func, *args, abandon_on_cancel: bool = False):
        """Run a blocking callable off-loop, bounded by the pool's capacity.

        Backpressure is implicit: when all slots are busy the await suspends
        until one frees — it never over-commits threads.
        """
        return await anyio.to_thread.run_sync(
            func, *args, abandon_on_cancel=abandon_on_cancel, limiter=self._limiter
        )

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def borrowed(self) -> int:
        """Slots currently in use (live gauge for status/governor)."""
        return int(self._limiter.borrowed_tokens)

    @property
    def available(self) -> int:
        return self._limit - self.borrowed
