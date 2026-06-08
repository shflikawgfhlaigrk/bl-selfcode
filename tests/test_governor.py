"""Governor admission gate — proves it actually sheds under a load storm.

The AceOS-killer was a load storm: the daemon kept admitting heavy work onto an
already-saturated box (the module docstring records "load 23.5 on 18 cores")
until it died. The gate must SHED heavy calls when the box is oversubscribed,
while cheap ping/status calls bypass so the daemon stays answerable.

Regression guard: the threshold shipped at 8.0/core, which on an 18-core box
needs load1 > 144 to fire — it never tripped, so the governor was wired but
effectively bypassed. These tests pin a protective threshold and the real
shed/admit behaviour around it.
"""
import pytest

from utah.daemon import daemon as daemon_mod
from utah.daemon.governor import Governor
from utah.daemon.rpc import OVERLOADED, RpcError


def _fixed_load(monkeypatch, load1: float) -> None:
    monkeypatch.setattr(
        "utah.daemon.governor.os.getloadavg", lambda: (load1, load1, load1)
    )


def test_production_threshold_is_protective():
    # Must be low enough to fire on a real storm on a many-core box. The old
    # 8.0 needed load > 8*ncpu (144 on 18 cores) and never tripped.
    assert daemon_mod.GOV_MAX_LOAD_PER_CORE <= 2.0


def test_sheds_when_oversubscribed(monkeypatch):
    gov = Governor(ncpu=18, max_load_per_core=1.5, max_inflight=64)
    _fixed_load(monkeypatch, 30.0)  # 1.67/core > 1.5 → oversubscribed
    with pytest.raises(RpcError) as ei:
        gov.admit()
    assert ei.value.code == OVERLOADED
    assert "shed" in str(ei.value).lower()


def test_admits_at_full_but_not_oversubscribed(monkeypatch):
    gov = Governor(ncpu=18, max_load_per_core=1.5, max_inflight=64)
    _fixed_load(monkeypatch, 18.0)  # 1.0/core — fully used, not oversubscribed
    gov.admit()  # must not raise


def test_inflight_cap_sheds_independent_of_load(monkeypatch):
    gov = Governor(ncpu=18, max_load_per_core=1.5, max_inflight=2)
    _fixed_load(monkeypatch, 1.0)  # load is fine; cap is the limiter
    gov._inflight = 2
    with pytest.raises(RpcError) as ei:
        gov.admit()
    assert ei.value.code == OVERLOADED


def test_snapshot_exposes_threshold(monkeypatch):
    # The deck/status must be able to show the live shed threshold (transparency).
    gov = Governor(ncpu=18, max_load_per_core=1.5, max_inflight=64)
    _fixed_load(monkeypatch, 9.0)
    snap = gov.snapshot()
    assert snap["max_load_per_core"] == 1.5
    assert snap["load_per_core"] == 0.5


def test_admission_contextmanager_releases_inflight(monkeypatch):
    gov = Governor(ncpu=18, max_load_per_core=1.5, max_inflight=64)
    _fixed_load(monkeypatch, 1.0)
    with gov.admission():
        assert gov._inflight == 1
    assert gov._inflight == 0
