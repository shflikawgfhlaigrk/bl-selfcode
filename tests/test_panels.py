"""Deck panel drill-down contracts (utah/daemon/handlers/panels.py).

The deck's JS renders whatever these handlers return, so the wire shape is a
contract: every handler is async, answers ``{"panel": <name>, ...}``, and the
system gauges degrade HONESTLY — a missing psutil or a down loadavg gauge is
reported as empty/None plus a note, never fabricated zeros that read as
"box idle". Collaborators behind the lazy imports (ledger, failures, mail)
are injected at their own seams; the handlers under test are always real.
"""
from __future__ import annotations

import inspect
import time

import anyio
import pytest

from utah.daemon.bus import Bus
from utah.daemon.dispatch import Context
from utah.daemon.governor import Governor
from utah.daemon.handlers import panels
from utah.daemon.pool import WorkerPool


@pytest.fixture
def ctx() -> Context:
    return Context(
        pool=WorkerPool(limit=2),
        governor=Governor(max_load_per_core=1e9, max_inflight=1000),
        bus=Bus(),
        shutdown=anyio.Event(),
        started_monotonic=time.monotonic(),
        version="test",
    )


def run(coro_fn, ctx):
    return anyio.run(lambda: coro_fn(ctx))


# -- registry shape -------------------------------------------------------------

def test_every_panel_handler_is_async():
    bad = sorted(
        name for name, fn in panels.PANEL_REGISTRY.items()
        if not inspect.iscoroutinefunction(fn)
    )
    assert not bad, f"non-async panel handlers: {bad}"


def test_registry_covers_the_deck_panels():
    # The deck's clickable panels (live.html drill-downs) must all resolve.
    expected = {
        "pool", "governor", "spine", "leads", "probate", "outreach",
        "engines", "audit", "memory", "voice", "trading", "lab",
    }
    missing = expected - set(panels.PANEL_REGISTRY)
    assert not missing, f"deck panels missing from PANEL_REGISTRY: {sorted(missing)}"


def test_registry_names_are_nonblank_strings():
    assert all(isinstance(k, str) and k.strip() for k in panels.PANEL_REGISTRY)


# -- _sys_detail: honest degradation ---------------------------------------------

def test_sys_detail_degrades_honestly_without_psutil(monkeypatch):
    monkeypatch.setattr(panels, "psutil", None)
    out = panels._sys_detail()
    assert out["per_core_pct"] == [] and out["top_processes"] == []
    assert out["note"].startswith("DEGRADED")
    assert isinstance(out["load1"], float)  # loadavg gauge is real, not faked


def test_sys_detail_reports_loadavg_gauge_down_as_none(monkeypatch):
    monkeypatch.setattr(panels, "psutil", None)

    def boom():
        raise OSError("load average unobtainable")

    monkeypatch.setattr(panels.os, "getloadavg", boom)
    out = panels._sys_detail()
    # None = "gauge down", never a fabricated 0.0 that reads as "box idle".
    assert out["load1"] is None and out["load5"] is None and out["load15"] is None


@pytest.mark.skipif(panels.psutil is None, reason="psutil not installed in this venv")
def test_sys_detail_full_gauges_with_psutil():
    out = panels._sys_detail()
    assert len(out["per_core_pct"]) >= 1
    assert len(out["top_processes"]) <= 8
    assert all({"pid", "name", "cpu"} <= set(p) for p in out["top_processes"])


# -- cheap panels against the real Context ---------------------------------------

def test_panel_pool_reports_real_slots(ctx):
    out = run(panels._panel_pool, ctx)
    assert out["panel"] == "pool"
    assert out["limit"] == 2 and out["busy"] == 0 and out["idle"] == 2
    assert [s["state"] for s in out["slots"]] == ["idle", "idle"]


def test_panel_spine_reports_version_uptime_and_bus(ctx):
    out = run(panels._panel_spine, ctx)
    assert out["panel"] == "spine"
    assert out["version"] == "test" and out["uptime_s"] >= 0.0
    assert out["draining"] is False
    assert out["bus_subscribers"] == 0


def test_panel_governor_merges_snapshot_and_sys_detail(ctx, monkeypatch):
    monkeypatch.setattr(panels, "psutil", None)  # deterministic, venv-independent
    out = run(panels._panel_governor, ctx)
    assert out["panel"] == "governor"
    assert out["max_inflight"] == 1000          # from the governor snapshot
    assert "load1" in out and "per_core_pct" in out  # from _sys_detail
    assert ctx.governor.snapshot()["inflight"] == 0  # read admission released


def test_panel_governor_runs_under_read_admission_not_heavy(ctx, monkeypatch):
    # Heavy work sheds at 1.5/core but the governor panel must still answer
    # (read tier) — the deck blanking bug this tier exists for.
    monkeypatch.setattr(panels, "psutil", None)
    monkeypatch.setattr(
        "utah.daemon.governor.os.getloadavg", lambda: (54.0, 54.0, 54.0)
    )
    gov = Governor(ncpu=18, max_load_per_core=1.5, max_inflight=64)
    stormy = Context(
        pool=ctx.pool, governor=gov, bus=ctx.bus, shutdown=ctx.shutdown,
        started_monotonic=ctx.started_monotonic, version="test",
    )
    out = run(panels._panel_governor, stormy)
    assert out["panel"] == "governor"


# -- ledger-backed panels via the injectable seam --------------------------------

class _StubLedger:
    def __init__(self, rows):
        self._rows = rows
        self.calls: list = []

    def recent(self, domain: str, limit: int) -> list:
        self.calls.append((domain, limit))
        return self._rows


@pytest.mark.parametrize("handler,panel,domain", [
    (panels._panel_leads, "leads", "leads"),
    (panels._panel_probate, "probate", "probate"),
    (panels._panel_outreach, "outreach", "outreach"),
    (panels._panel_engines, "engines", "fires"),
])
def test_ledger_panels_return_rows_for_their_domain(ctx, monkeypatch, handler, panel, domain):
    stub = _StubLedger([{"name": "row1"}])
    monkeypatch.setattr("utah.product.ledger.get_ledger", lambda **kw: stub)
    out = run(handler, ctx)
    assert out == {"panel": panel, "rows": [{"name": "row1"}]}
    assert stub.calls == [(domain, 50)]


def test_panel_audit_rows_reflect_documented_failures(ctx):
    from utah import failures

    failures.record("panels_test", "synthetic", "panel audit drill-down proof")
    out = run(panels._panel_audit, ctx)
    assert out["panel"] == "audit"
    assert any(
        r["source"] == "panels_test" and r["kind"] == "synthetic" for r in out["rows"]
    )


def test_panel_mail_is_honestly_gated_without_creds(ctx, monkeypatch):
    monkeypatch.setattr("utah.mail.creds_available", lambda: False)
    out = run(panels._panel_mail, ctx)
    assert out["panel"] == "mail" and out["status"] == "gated"
    assert "GATED" in out["note"]


def test_panel_mail_ready_with_creds(ctx, monkeypatch):
    monkeypatch.setattr("utah.mail.creds_available", lambda: True)
    out = run(panels._panel_mail, ctx)
    assert out["status"] == "ready"
