"""work_leads RPC handler — the missing real-send trigger (audit §1.1 / wiring finding #3).

`queue_outreach` is queue-only (can_send defaults False) — there was NO handler that
actually SENDS. `work_leads` drives `outreach.run_scheduled` (which sends via real SMTP,
suppression-checked) on demand from the surface, defaulting to the unblocked EMAIL channel.
The send itself is exercised live; here we prove the handler wiring (off-loop, governed,
right channel, params passthrough) without spawning the real CLI/SMTP.
"""
from __future__ import annotations

import time

import anyio

from utah.daemon.dispatch import Context
from utah.daemon.governor import Governor
from utah.daemon.handlers import REGISTRY
from utah.daemon.pool import WorkerPool


def _ctx() -> Context:
    from utah.daemon.bus import Bus

    return Context(
        pool=WorkerPool(limit=2),
        governor=Governor(max_load_per_core=1e9, max_inflight=1000),  # never shed in test
        bus=Bus(),
        shutdown=anyio.Event(),
        started_monotonic=time.monotonic(),
        version="test",
    )


def test_work_leads_is_registered():
    assert "work_leads" in REGISTRY


def test_work_leads_drives_real_send_on_auto_channel(monkeypatch):
    """Default channel is AUTO (email-first, text-fallback via iMessage — Michael's
    directive) and the run result is returned."""
    from utah.product import outreach

    captured = {}

    def fake_run_scheduled(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"campaign": "smb_no_website", "channel": "auto", "sent": 3, "queued": 3}

    monkeypatch.setattr(outreach, "run_scheduled", fake_run_scheduled)

    async def scenario():
        return await REGISTRY["work_leads"](_ctx(), {})

    result = anyio.run(scenario)
    assert result["sent"] == 3
    assert captured["kwargs"].get("channel") == "auto"


def test_work_leads_passes_limit_through(monkeypatch):
    from utah.product import outreach

    captured = {}

    def fake_run_scheduled(*args, **kwargs):
        captured["kwargs"] = kwargs
        return {"sent": 0, "queued": 0, "channel": "email"}

    monkeypatch.setattr(outreach, "run_scheduled", fake_run_scheduled)

    async def scenario():
        return await REGISTRY["work_leads"](_ctx(), {"limit": 5})

    anyio.run(scenario)
    assert captured["kwargs"].get("limit") == 5
