"""The web bridge's deck-state builder: ONE live backend feed covering every
panel domain. Real where a producer exists (spine/daemon/memory from the live
daemon), honest-empty where none has landed yet (real-or-DORMANT, never faked),
so a new producer lights its panel with no frontend change."""
from __future__ import annotations

from utah.interface import web

# every panel on the deck maps to a domain in the single /state feed
PANEL_DOMAINS = {
    "health", "spine", "daemon", "memory",       # live (real producers exist)
    "engines", "agents", "risk", "voice", "leads",  # domain feeds
    "probate", "outreach", "audit", "tick", "sync", "events",
}

ST = {"version": "0.1.0", "uptime_s": 1.0, "memory": {"total": 3, "live": 2}}


def test_deck_state_exposes_every_panel_domain():
    missing = PANEL_DOMAINS - set(web._deck_state(ST))
    assert not missing, f"missing panel domains: {missing}"


def test_deck_state_health_live_when_daemon_up():
    assert web._deck_state(ST)["health"] == "live"


def test_deck_state_health_down_when_daemon_unreachable():
    assert web._deck_state(None)["health"] == "down"


def test_deck_state_no_producer_domains_are_honest_empty_lists():
    d = web._deck_state(ST)
    for k in ["engines", "agents", "leads", "probate", "outreach", "tick",
              "audit", "sync", "events"]:
        assert d[k] == [], f"{k} must be an honest-empty list (DORMANT), got {d[k]!r}"


def test_deck_state_passes_through_live_memory_and_spine():
    d = web._deck_state(ST)
    assert d["memory"] == {"total": 3, "live": 2}
    assert d["spine"] == ST and d["daemon"] == ST


def test_deck_state_when_daemon_down_spine_is_empty_not_fabricated():
    d = web._deck_state(None)
    assert d["spine"] == {} and d["memory"] == {}


def test_api_alias_serves_same_feed_and_unknown_routes_404(monkeypatch):
    """/api/state must equal /state (external monitors probe the /api spelling),
    and an unknown route must be 404 — the old 200+{} catch-all made a healthy
    deck look data-dead to every outside auditor."""
    from starlette.testclient import TestClient

    from utah.interface import web

    async def no_daemon():
        return None

    async def no_snapshot():
        return {}

    class DeadCtl:
        async def call(self, *a, **k):
            raise RuntimeError("daemon down")

    monkeypatch.setattr(web, "_daemon_status", no_daemon)
    monkeypatch.setattr(web, "_ledger_snapshot", no_snapshot)
    monkeypatch.setattr(web, "ctl", DeadCtl())
    monkeypatch.setattr(web, "_audit_rows", lambda: [])

    client = TestClient(web.build_app())
    assert client.get("/api/state").json() == client.get("/state").json()
    assert client.get("/api/spine").status_code == 200      # state slice, both spellings
    missing = client.get("/definitely-not-a-route")
    assert missing.status_code == 404
    assert "unknown route" in missing.json()["error"]


def test_shed_serves_last_good_snapshot_labeled_degraded(monkeypatch):
    """A load-governor shed must NOT blank the deck: 4,666 real leads rendered as
    'no producer wired' for a whole load storm. The bridge keeps the last good
    snapshot and serves it labeled ``degraded`` so the deck can say BUSY."""
    import asyncio

    from utah.daemon.rpc import OVERLOADED, RpcError

    calls = {"n": 0}

    class FlakyCtl:
        async def call(self, method, *a, **k):
            calls["n"] += 1
            if calls["n"] <= 1:
                return {"counts": {"leads": 4666}, "leads": [{"name": "Atlas HVAC"}],
                        "probate": [], "outreach": [], "fires": []}
            raise RpcError(OVERLOADED, "shed: load 54.0 over 1.5×18 cores")

    monkeypatch.setattr(web, "ctl", FlakyCtl())
    monkeypatch.setattr(web, "_last_good", {})

    good = asyncio.run(web._ledger_snapshot())
    assert good["leads"][0]["name"] == "Atlas HVAC" and "degraded" not in good

    shed = asyncio.run(web._ledger_snapshot())
    assert shed["leads"][0]["name"] == "Atlas HVAC"   # stale-but-real, not {}
    assert "shed" in shed["degraded"]


def test_shed_with_no_cache_is_still_honest_empty(monkeypatch):
    import asyncio

    from utah.daemon.rpc import OVERLOADED, RpcError

    class ShedCtl:
        async def call(self, *a, **k):
            raise RpcError(OVERLOADED, "shed: load 54.0 over 1.5×18 cores")

    monkeypatch.setattr(web, "ctl", ShedCtl())
    monkeypatch.setattr(web, "_last_good", {})
    assert asyncio.run(web._ledger_snapshot()) == {}   # never fabricated


def test_memory_endpoint_degrades_to_last_good_counts(monkeypatch):
    from starlette.testclient import TestClient

    class FlakyCtl:
        def __init__(self):
            self.n = 0

        async def call(self, method, *a, **k):
            self.n += 1
            if self.n <= 1:
                return {"total": 9755, "live": 9100, "entities": 4200}
            raise RuntimeError("shed: load 54.0 over 1.5×18 cores")

    monkeypatch.setattr(web, "ctl", FlakyCtl())
    monkeypatch.setattr(web, "_last_good", {})
    client = TestClient(web.build_app())

    first = client.get("/memory")
    assert first.status_code == 200 and first.json()["total"] == 9755

    second = client.get("/memory")                     # shed → cached + degraded, not 503
    assert second.status_code == 200
    assert second.json()["total"] == 9755 and "shed" in second.json()["degraded"]


def test_csrf_guard_blocks_cross_origin_state_change(monkeypatch):
    """A drive-by browser page must not POST to /api/selfcode/edit or /api/console:
    a request carrying a cross-origin Origin header is rejected 403 before the
    handler runs. Same-origin and native (no-Origin) callers are unaffected."""
    from starlette.testclient import TestClient

    from utah.interface import web

    client = TestClient(web.build_app())

    # Cross-origin browser attack → 403, handler never reached.
    evil = client.post("/api/console", json={"cmd": "history"},
                       headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403 and "origin" in evil.json()["error"].lower()

    edit = client.post("/api/selfcode/edit", json={"instruction": "x"},
                       headers={"Origin": "http://attacker.test"})
    assert edit.status_code == 403

    # Same-origin deck fetch (Origin matches Host) is allowed through the guard
    # (it may still 4xx/5xx downstream, but NOT 403-for-origin).
    same = client.post("/api/tell", json={"text": ""},
                       headers={"Origin": "http://testserver", "Host": "testserver"})
    assert same.status_code != 403

    # Native client with no Origin header (curl, tailnet tap, daemon) is allowed.
    native = client.post("/api/tell", json={"text": ""})
    assert native.status_code != 403


def test_csrf_guard_leaves_get_routes_untouched(monkeypatch):
    """Read routes never carry a state change — the guard must not touch them even
    with a foreign Origin (monitors/embeds legitimately GET cross-origin)."""
    from starlette.testclient import TestClient

    from utah.interface import web

    async def no_daemon():
        return None

    monkeypatch.setattr(web, "_daemon_status", no_daemon)
    monkeypatch.setattr(web, "_ledger_snapshot", lambda: {})
    client = TestClient(web.build_app())
    r = client.get("/status", headers={"Origin": "https://monitor.example"})
    assert r.status_code == 200
