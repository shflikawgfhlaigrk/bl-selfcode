"""Global resource governor — one admission gate for heavy work.

Ace ran ~120 resident processes with nothing budgeting total concurrency →
load 23.5 on 18 cores (the "load storm"). Utah caps heavy work at the door:
before a brain turn / agent is admitted, the governor checks **live** system
load and a hard in-flight ceiling and sheds the call with ``OVERLOADED`` (a
typed JSON-RPC error the caller can back off on) rather than piling onto an
overloaded box. Cheap control calls (``ping``/``status``) bypass the gate, so
the daemon stays answerable even while shedding heavy work.
"""
from __future__ import annotations

import contextlib
import os

from utah.daemon.rpc import OVERLOADED, RpcError


class Governor:
    """Live load + in-flight admission gate for heavy handlers."""

    def __init__(
        self,
        *,
        ncpu: int | None = None,
        max_load_per_core: float = 8.0,
        max_inflight: int = 64,
    ) -> None:
        self._ncpu = ncpu or os.cpu_count() or 1
        self._max_load_per_core = max_load_per_core
        self._max_inflight = max_inflight
        self._inflight = 0

    def snapshot(self) -> dict:
        """Live gauges for ``status`` (never cached)."""
        load1 = os.getloadavg()[0]
        return {
            "load1": round(load1, 2),
            "load_per_core": round(load1 / self._ncpu, 3),
            "inflight": self._inflight,
            "max_inflight": self._max_inflight,
            "ncpu": self._ncpu,
        }

    def admit(self) -> None:
        """Admit one heavy call or raise :class:`RpcError` (``OVERLOADED``)."""
        if self._inflight >= self._max_inflight:
            raise RpcError(
                OVERLOADED,
                f"shed: {self._inflight} in-flight >= cap {self._max_inflight}",
                {"inflight": self._inflight},
            )
        load1 = os.getloadavg()[0]
        if load1 / self._ncpu > self._max_load_per_core:
            raise RpcError(
                OVERLOADED,
                f"shed: load {load1:.1f} over {self._max_load_per_core}×{self._ncpu} cores",
                {"load1": round(load1, 2)},
            )

    @contextlib.contextmanager
    def admission(self):
        """Admit + count one heavy call for its duration (always released)."""
        self.admit()
        self._inflight += 1
        try:
            yield
        finally:
            self._inflight -= 1
