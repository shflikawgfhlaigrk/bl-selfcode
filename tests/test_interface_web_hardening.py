"""Web-bridge hardening: malformed bodies are 400s (never 500 tracebacks), the
memory drill-down's limit/offset are CLAMPED before they reach the daemon, the
in-process self-code job table stays bounded under concurrent enqueues, and the
deck/PWA contract routes answer with the right shapes."""
from __future__ import annotations

import asyncio
import json

import pytest
from starlette.testclient import TestClient

from utah.interface import web


class CapturingCtl:
    """Records every RPC; answers with a canned per-method payload."""

    def __init__(self, replies=None):
        self.calls = []
        self.replies = replies or {}

    async def call(self, method, params=None, timeout=None):
        self.calls.append({"method": method, "params": params, "timeout": timeout})
        if method in self.replies:
            return self.replies[method]
        raise RuntimeError(f"no reply for {method}")


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(web, "_last_good", {})
    return TestClient(web.build_app(), raise_server_exceptions=False)


# --- malformed input is a 400, not a 500 ---------------------------------------

def test_api_tell_malformed_json_is_400(client):
    r = client.post("/api/tell", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert "error" in r.json()


def test_api_tell_empty_text_is_400(client):
    r = client.post("/api/tell", json={"text": "   "})
    assert r.status_code == 400


def test_api_speak_malformed_json_is_400_shape(client):
    r = client.post("/api/speak", content=b"\xff\xfe", headers={"Content-Type": "application/json"})
    assert r.status_code == 400          # garbage body == empty text == reject


def test_api_selfcode_edit_empty_is_400(client):
    r = client.post("/api/selfcode/edit", json={"text": ""})
    assert r.status_code == 400


# --- memory drill-down bounds ----------------------------------------------------

def test_memory_list_clamps_limit_and_offset(monkeypatch, client):
    ctl = CapturingCtl({"memory_list": {"rows": []}})
    monkeypatch.setattr(web, "ctl", ctl)
    assert client.get("/memory/list?limit=999999&offset=-5").status_code == 200
    params = ctl.calls[0]["params"]
    assert 1 <= params["limit"] <= 500          # a 1M-row read can never reach the daemon
    assert params["offset"] >= 0


def test_memory_list_non_integer_is_400(client):
    assert client.get("/memory/list?limit=lots").status_code == 400


# --- self-code job table ----------------------------------------------------------

def test_job_table_stays_bounded_under_enqueue_burst(monkeypatch, client):
    monkeypatch.setattr(web, "_run_edit_job", lambda job_id, text: None)
    monkeypatch.setattr(web, "_EDIT_JOBS", {
        f"old{i}": {"id": f"old{i}", "status": "done", "finished": float(i)}
        for i in range(web._EDIT_JOBS_MAX)
    })
    r = client.post("/api/selfcode/edit", json={"text": "tidy the docstring"})
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert len(web._EDIT_JOBS) <= web._EDIT_JOBS_MAX
    assert "old0" not in web._EDIT_JOBS          # the OLDEST finished evicted first
    assert r.json()["job"] in web._EDIT_JOBS


def test_unknown_job_is_404(client):
    assert client.get("/api/selfcode/job/nope").status_code == 404


def test_job_poll_reports_elapsed(monkeypatch, client):
    monkeypatch.setattr(web, "_EDIT_JOBS",
                        {"j1": {"id": "j1", "status": "working", "started": 0.0}})
    body = client.get("/api/selfcode/job/j1").json()
    assert body["status"] == "working" and body["elapsed_s"] > 0


# --- PWA / deck contract ----------------------------------------------------------

def test_index_injects_pwa_head_exactly_once(client):
    html = client.get("/").text
    assert html.count('rel="manifest"') == 1
    assert '<link rel="apple-touch-icon"' in html


def test_manifest_and_sw_and_icon_routes(client):
    m = client.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.json()["display"] == "standalone"
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]
    icon = client.get("/icon-192.png")
    assert icon.status_code == 200 and icon.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_health_route_shape_when_down(monkeypatch, client):
    async def no_daemon():
        return None

    monkeypatch.setattr(web, "_daemon_status", no_daemon)
    monkeypatch.setattr(web, "ctl", CapturingCtl())
    monkeypatch.setattr(web, "_audit_rows", lambda: [])
    body = client.get("/health").json()
    assert body == {"status": "down", "ok": False}


def test_deck_serves_ledger_slices_when_daemon_up(monkeypatch, client):
    snap = {"counts": {"leads": 2}, "leads": [{"name": "Atlas HVAC"}, {"name": "B"}],
            "probate": [], "outreach": [], "fires": [{"engine": "meanrev"}]}
    ctl = CapturingCtl({"status": {"version": "1", "uptime_s": 5.0},
                        "memory_stats": {"total": 3, "live": 2},
                        "ledger_snapshot": snap})
    monkeypatch.setattr(web, "ctl", ctl)
    monkeypatch.setattr(web, "_audit_rows", lambda: ["row"])

    state = client.get("/state").json()
    assert state["health"] == "live"
    assert [r["name"] for r in state["leads"]] == ["Atlas HVAC", "B"]
    assert state["engines"] == [{"engine": "meanrev"}]   # 'engines' panel = fires
    assert client.get("/leads").json() == state["leads"]  # slice == whole-feed slice


# --- streaming endpoints stay honest on failure ------------------------------------

def test_tell_stream_reports_unreachable_brain_honestly(monkeypatch, client):
    class DeadStream:
        async def call(self, *a, **k):
            raise RuntimeError("nope")

        def tell_stream(self, text):
            async def gen():
                raise RuntimeError("daemon down")
                yield  # pragma: no cover

            return gen()

    monkeypatch.setattr(web, "ctl", DeadStream())
    from utah import failures
    from tests.fakes import FakeFailureStore

    failures.set_store(FakeFailureStore())
    r = client.get("/api/tell/stream?q=hello")
    assert r.status_code == 200
    assert "brain unreachable" in r.text                 # honest, not fabricated
    assert "event: done" in r.text                       # stream is properly terminated
