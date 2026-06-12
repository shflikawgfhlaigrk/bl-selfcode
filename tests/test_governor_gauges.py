"""Governor gauge failure + release invariants (complements test_governor.py).

Two contracts test_governor.py does not pin:

1. **Gauge-down behaviour.** ``os.getloadavg`` can raise ``OSError`` ("load
   average unobtainable"). The load gate must then degrade OPEN (the in-flight
   cap still bounds work — a dead gauge must not become a permanent outage)
   and ``snapshot`` must report the gauge as down honestly (``None``), never a
   fabricated ``0.0`` that reads as "box idle".

2. **Release on exception.** ``admission()``/``read_admission()`` must release
   the in-flight slot when the governed body raises — a leaking slot would
   ratchet ``_inflight`` up to the cap and brick all heavy work.
"""
from __future__ import annotations

import pytest

from utah.daemon.governor import Governor
from utah.daemon.rpc import OVERLOADED, RpcError


def _gauge_down(monkeypatch) -> None:
    def boom():
        raise OSError("load average unobtainable")

    monkeypatch.setattr("utah.daemon.governor.os.getloadavg", boom)


def _fixed_load(monkeypatch, load1: float) -> None:
    monkeypatch.setattr(
        "utah.daemon.governor.os.getloadavg", lambda: (load1, load1, load1)
    )


# -- gauge down ---------------------------------------------------------------

def test_admit_degrades_open_when_load_gauge_is_down(monkeypatch):
    gov = Governor(ncpu=18, max_load_per_core=1.5, max_inflight=64)
    _gauge_down(monkeypatch)
    gov.admit()  # must NOT raise: a dead gauge is not a permanent outage


def test_inflight_cap_still_sheds_when_gauge_is_down(monkeypatch):
    gov = Governor(ncpu=18, max_load_per_core=1.5, max_inflight=2)
    _gauge_down(monkeypatch)
    gov._inflight = 2
    with pytest.raises(RpcError) as ei:
        gov.admit()
    assert ei.value.code == OVERLOADED


def test_snapshot_reports_gauge_down_honestly_not_zero(monkeypatch):
    gov = Governor(ncpu=18, max_load_per_core=1.5, max_inflight=64)
    _gauge_down(monkeypatch)
    snap = gov.snapshot()
    # None = "gauge down", never a fabricated 0.0 that reads as "box idle".
    assert snap["load1"] is None
    assert snap["load_per_core"] is None
    # The static gauges stay real.
    assert snap["ncpu"] == 18 and snap["max_inflight"] == 64


def test_snapshot_live_values_unchanged_when_gauge_works(monkeypatch):
    gov = Governor(ncpu=18, max_load_per_core=1.5, max_inflight=64)
    _fixed_load(monkeypatch, 9.0)
    snap = gov.snapshot()
    assert snap["load1"] == 9.0 and snap["load_per_core"] == 0.5


# -- release on exception -----------------------------------------------------

def test_admission_releases_inflight_when_body_raises(monkeypatch):
    gov = Governor(ncpu=18, max_load_per_core=1.5, max_inflight=64)
    _fixed_load(monkeypatch, 1.0)
    with pytest.raises(RuntimeError):
        with gov.admission():
            assert gov._inflight == 1
            raise RuntimeError("handler crashed mid-flight")
    assert gov._inflight == 0  # the slot must come back


def test_read_admission_releases_inflight_when_body_raises(monkeypatch):
    gov = Governor(ncpu=18, max_load_per_core=1.5, max_inflight=64)
    _fixed_load(monkeypatch, 1.0)
    with pytest.raises(RuntimeError):
        with gov.read_admission():
            raise RuntimeError("read crashed mid-flight")
    assert gov._inflight == 0


def test_nested_admissions_count_and_release(monkeypatch):
    gov = Governor(ncpu=18, max_load_per_core=1.5, max_inflight=64)
    _fixed_load(monkeypatch, 1.0)
    with gov.admission():
        with gov.read_admission():
            assert gov.snapshot()["inflight"] == 2
        assert gov.snapshot()["inflight"] == 1
    assert gov.snapshot()["inflight"] == 0


def test_admit_honors_per_call_load_limit(monkeypatch):
    gov = Governor(ncpu=10, max_load_per_core=1.5, max_inflight=64)
    _fixed_load(monkeypatch, 30.0)  # 3.0/core
    with pytest.raises(RpcError):
        gov.admit()  # default heavy limit (1.5) sheds
    gov.admit(max_load_per_core=12.0)  # the read-tier limit admits
