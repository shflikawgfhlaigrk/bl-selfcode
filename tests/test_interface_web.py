"""The web bridge's deck-state builder: ONE live backend feed covering every
panel domain. Real where a producer exists (spine/daemon/memory from the live
daemon), honest-empty where none has landed yet (real-or-DORMANT, never faked),
so a new producer lights its panel with no frontend change."""
from __future__ import annotations

import json

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


def test_operator_snapshot_reads_operator_json(tmp_path, monkeypatch):
    """operator.json outcome + mail_gated must reach the deck via /state."""
    payload = {
        "ts": 1718380800.0,
        "ok": True,
        "mail_gated": True,
        "outcome": {
            "ok": False,
            "assessable": True,
            "sends": 269,
            "sales": 0,
            "window_h": 26,
            "reason": "NO outcome in 26h — 269 sends, 0 sales",
        },
    }
    (tmp_path / "operator.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(web.runtime, "RUN_DIR", tmp_path)

    snap = web._operator_snapshot()
    assert snap["present"] is True
    assert snap["mail_gated"] is True
    assert snap["revenue_ok"] is False
    assert snap["outcome"]["sends"] == 269


def test_state_includes_operator_snapshot(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    (tmp_path / "operator.json").write_text(
        json.dumps({"ok": True, "mail_gated": False,
                    "outcome": {"ok": True, "sends": 3, "sales": 1, "window_h": 26}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(web.runtime, "RUN_DIR", tmp_path)

    async def no_daemon():
        return None

    async def no_snapshot():
        return {}

    monkeypatch.setattr(web, "_daemon_status", no_daemon)
    monkeypatch.setattr(web, "_ledger_snapshot", no_snapshot)
    monkeypatch.setattr(web, "_audit_rows", lambda: [])

    client = TestClient(web.build_app())
    op = client.get("/state").json()["operator"]
    assert op["present"] is True
    assert op["revenue_ok"] is True
    assert op["mail_gated"] is False


def test_operator_snapshot_honest_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(web.runtime, "RUN_DIR", tmp_path)
    assert web._operator_snapshot() == {"present": False}


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

    # A PRESENT but unparseable/empty-host Origin must be REFUSED, not waved through
    # as if it were native — a present Origin that doesn't clearly match is hostile.
    for bad in ("http://", "null", "://nohost"):
        r = client.post("/api/console", json={"cmd": "history"},
                        headers={"Origin": bad})
        assert r.status_code == 403, f"empty/garbage Origin {bad!r} bypassed the guard"

    # Hostnames are case-INSENSITIVE (RFC 3986/7230): a same-origin request whose
    # Origin and Host differ only in case must be ALLOWED, never false-rejected (a
    # mixed-case tailnet hostname would otherwise lock the deck out of itself).
    cased = client.post("/api/tell", json={"text": ""},
                        headers={"Origin": "http://TestServer", "Host": "testserver"})
    assert cased.status_code != 403


def test_csrf_guard_leaves_get_routes_untouched(monkeypatch):
    """Read routes never carry a state change — the guard must not touch them even
    with a foreign Origin (monitors/embeds legitimately GET cross-origin). The guard's
    contract is 'GET is never 403'; whether /status is 200 or 503 depends on the
    daemon, which this test must NOT depend on (api_status calls ctl.call directly —
    asserting ==200 only passed because a live daemon happened to answer)."""
    from starlette.testclient import TestClient

    from utah.interface import web

    class FakeCtl:
        async def call(self, *a, **k):
            return {"ok": True}                 # hermetic: no live daemon needed

    monkeypatch.setattr(web, "ctl", FakeCtl())
    client = TestClient(web.build_app())
    r = client.get("/status", headers={"Origin": "https://monitor.example"})
    assert r.status_code != 403                  # the guard never blocks a GET
    assert r.status_code == 200                  # and with ctl answering, it serves


def test_post_endpoints_reject_oversized_input():
    """Unbounded POST text is a DoS/abuse surface — every state-changing text
    endpoint must reject input over the content cap with 413, before doing any work."""
    from starlette.testclient import TestClient

    from utah import config
    from utah.interface import web

    client = TestClient(web.build_app())
    huge = "x" * (config.MAX_CONTENT_CHARS + 1)
    for path, key in [("/api/tell", "text"), ("/api/console", "line"),
                      ("/api/selfcode/edit", "text"), ("/api/speak", "text")]:
        r = client.post(path, json={key: huge}, headers={"Origin": "http://testserver",
                                                         "Host": "testserver"})
        assert r.status_code == 413, f"{path} accepted oversized input ({r.status_code})"
        assert "too large" in r.json().get("error", "").lower()
