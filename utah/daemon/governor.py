"""Global resource governor — one admission gate for heavy work.

Ace ran ~120 resident processes with nothing budgeting total concurrency →
load 23.5 on 18 cores (the "load storm"). Utah caps heavy work at the door:
before a brain turn / agent is admitted, the governor checks **live** system
load and a hard in-flight ceiling and sheds the call with ``OVERLOADED`` (a
typed JSON-RPC error the caller can back off on) rather than piling onto an
overloaded box. Cheap control calls (``ping``/``status``) bypass the gate, so
the daemon stays answerable even while shedding heavy work.

Reads are a third tier: deck/panel row reads are ~30ms LIMIT-N selects, but
governing them at the heavy threshold blanked the whole deck whenever EXTERNAL
load (other apps) crossed 1.5×cores — real data rendered as "no producer".
``read_admission`` keeps the in-flight cap (a stuck Postgres still can't pile
up pool threads) and sheds only in a genuine storm.
"""
from __future__ import annotations

import contextlib
import os

from utah.daemon.rpc import OVERLOADED, RpcError

#: Cheap-read shed threshold — on 18 cores this means load1 > 216. Heavy work
#: sheds at 1.5×cores long before reads do; by the time reads shed, the box is
#: in a storm where even a 30ms select can't be trusted to return promptly.
READ_MAX_LOAD_PER_CORE = 12.0


class Governor:
    """Live load + in-flight admission gate for heavy handlers."""

    def __init__(
        self,
        *,
        ncpu: int | None = None,
        max_load_per_core: float = 1.5,
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
            "max_load_per_core": self._max_load_per_core,
            "inflight": self._inflight,
            "max_inflight": self._max_inflight,
            "ncpu": self._ncpu,
        }

    def admit(self, *, max_load_per_core: float | None = None) -> None:
        """Admit one heavy call or raise :class:`RpcError` (``OVERLOADED``)."""
        if self._inflight >= self._max_inflight:
            raise RpcError(
                OVERLOADED,
                f"shed: {self._inflight} in-flight >= cap {self._max_inflight}",
                {"inflight": self._inflight},
            )
        limit = self._max_load_per_core if max_load_per_core is None else max_load_per_core
        load1 = os.getloadavg()[0]
        if load1 / self._ncpu > limit:
            raise RpcError(
                OVERLOADED,
                f"shed: load {load1:.1f} over {limit}×{self._ncpu} cores",
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

    @contextlib.contextmanager
    def read_admission(self):
        """Admit + count one cheap read; sheds only in a true load storm."""
        self.admit(max_load_per_core=READ_MAX_LOAD_PER_CORE)
        self._inflight += 1
        try:
            yield
        finally:
            self._inflight -= 1
