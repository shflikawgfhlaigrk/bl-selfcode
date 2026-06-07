"""Discord provisioner + feed bridge — proven with zero network.

A stateful FakeSession simulates a guild so idempotency is REAL: the first provision
creates the whole blueprint, the second creates nothing (everything already exists).
Honest gate (no token / no webhook → gated, never faked), correct read-only/private
permission overwrites, and webhook posting via an injected transport.
"""
from __future__ import annotations

import json

from utah import config, failures
from utah.integrations import discord, discord_feed
from tests.fakes import FakeFailureStore


# --- a stateful fake of the Discord REST API ---------------------------------
class FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.headers = {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    """Minimal stateful Discord guild: roles/channels/webhooks persist across calls."""

    def __init__(self, guild_id="G1", guilds=None):
        self.guild_id = guild_id
        self.roles = []           # created roles
        self.channels = []        # created channels (categories + channels)
        self.webhooks = {}        # channel_id -> [webhook]
        self.messages = []        # posted messages
        self.guilds = guilds if guilds is not None else [{"id": guild_id}]
        self._n = 0

    def _id(self):
        self._n += 1
        return f"{1000 + self._n}"

    def request(self, method, url, headers=None, json=None, timeout=30):
        path = url.split("/api/v10", 1)[1] if "/api/v10" in url else url
        body = json or {}

        if method == "GET" and path == "/users/@me/guilds":
            return FakeResp(200, self.guilds)
        if method == "POST" and path == "/guilds":
            self.guild_id = self._id()
            return FakeResp(201, {"id": self.guild_id, "name": body.get("name")})

        if path.endswith("/roles") and path.startswith("/guilds"):
            if method == "GET":
                return FakeResp(200, list(self.roles))
            r = {"id": self._id(), **body}
            self.roles.append(r)
            return FakeResp(201, r)

        if path.endswith("/channels") and path.startswith("/guilds"):
            if method == "GET":
                return FakeResp(200, list(self.channels))
            c = {"id": self._id(), **body}
            self.channels.append(c)
            return FakeResp(201, c)

        if path.startswith("/channels/"):
            parts = path.split("/")
            cid = parts[2]
            if path.endswith("/webhooks"):
                if method == "GET":
                    return FakeResp(200, list(self.webhooks.get(cid, [])))
                h = {"id": self._id(), "token": f"tok-{cid}", "name": body.get("name")}
                self.webhooks.setdefault(cid, []).append(h)
                return FakeResp(201, h)
            if path.endswith("/messages") and method == "POST":
                self.messages.append((cid, body))
                return FakeResp(200, {"id": self._id()})
            if method == "PATCH":  # edit channel
                for c in self.channels:
                    if c["id"] == cid:
                        c.update(body)
                        return FakeResp(200, c)
                return FakeResp(404, {"message": "unknown channel"})

        return FakeResp(404, {"message": f"unhandled {method} {path}"})


def _token(monkeypatch, **over):
    creds = {"bot_token": "TOK", "guild_id": "G1"}
    creds.update(over)
    monkeypatch.setattr(discord, "_load_creds", lambda: creds)
    return creds


# --- blueprint / dry-run -----------------------------------------------------
def test_dry_run_mirrors_the_website_no_network():
    rep = discord.provision(dry_run=True)
    t = rep["totals"]
    assert t["roles"] == 6
    assert t["categories"] == 9
    assert t["channels"] == 34
    assert t["webhooks"] == 19
    # the website's domains are all present as categories
    cats = " ".join(rep["created_categories"])
    for domain in ("WELCOME", "BRAIN", "REVENUE", "TRADING", "SPINE", "SELF-CODE", "VOICE"):
        assert domain in cats


# --- honest gate -------------------------------------------------------------
def test_gated_without_token(monkeypatch):
    store = FakeFailureStore(); failures.set_store(store)
    monkeypatch.setattr(discord, "_load_creds", lambda: None)
    rep = discord.provision()
    assert rep["gated"] is True
    assert any("gated" in row[1] or "gated" in row[2] for row in store.rows)


# --- provisioning is idempotent ----------------------------------------------
def test_provision_builds_then_is_idempotent(monkeypatch):
    failures.set_store(FakeFailureStore())
    _token(monkeypatch)
    sess = FakeSession(guild_id="G1")

    first = discord.provision(guild_id="G1", session=sess)
    assert first["gated"] is False and not first["errors"]
    assert len(first["created_roles"]) == 6
    assert len(first["created_categories"]) == 9
    assert len(first["created_channels"]) == 34
    assert len(first["webhooks"]) == 19          # every feed channel got a webhook

    # second run against the SAME live guild: nothing new is created
    second = discord.provision(guild_id="G1", session=sess)
    assert second["created_roles"] == []
    assert second["created_categories"] == []
    assert second["created_channels"] == []
    assert len(second["existing_channels"]) == 34
    assert len(second["existing_roles"]) == 6


def test_create_guild_when_none_known(monkeypatch):
    failures.set_store(FakeFailureStore())
    _token(monkeypatch, guild_id=None)
    sess = FakeSession(guilds=[])
    rep = discord.provision(create=True, session=sess)
    assert rep["gated"] is False and rep["guild_id"]
    assert len(rep["created_channels"]) == 34


def test_ambiguous_guilds_is_an_honest_error(monkeypatch):
    failures.set_store(FakeFailureStore())
    _token(monkeypatch, guild_id=None)
    sess = FakeSession(guilds=[{"id": "A"}, {"id": "B"}])
    rep = discord.provision(session=sess)
    assert rep["errors"] and "guilds" in rep["errors"][0]


# --- permission overwrites express the read-only / private intent ------------
def test_readonly_and_private_overwrites():
    roles = {discord.R_OPERATOR: "10", discord.R_ACE: "11", discord.R_MUTED: "12"}
    ro = discord._overwrites("EVERY", roles, discord.Channel("x", readonly=True))
    everyone = next(o for o in ro if o["id"] == "EVERY")
    assert int(everyone["deny"]) & discord.P_SEND_MESSAGES        # @everyone can't send
    assert int(everyone["allow"]) & discord.P_VIEW_CHANNEL        # but can view

    pv = discord._overwrites("EVERY", roles, discord.Channel("y", private=True))
    everyone = next(o for o in pv if o["id"] == "EVERY")
    assert int(everyone["deny"]) & discord.P_VIEW_CHANNEL          # @everyone can't even see


def test_webhook_post_uses_injected_transport(monkeypatch):
    failures.set_store(FakeFailureStore())
    seen = {}
    def post(url, payload):
        seen["url"], seen["payload"] = url, payload
        return True
    ok = discord.post("https://discord.com/api/webhooks/1/abc", "hello", http_post=post)
    assert ok is True
    assert seen["payload"]["content"] == "hello"


# --- feed bridge -------------------------------------------------------------
def test_feed_gated_without_webhooks(monkeypatch):
    failures.set_store(FakeFailureStore())
    monkeypatch.setattr(discord_feed, "_load_hooks", lambda: {})
    assert discord_feed.available() is False
    assert discord_feed.feed_lead({"name": "Acme"}) is False    # gate, not fake


def test_feed_publishes_lead_embed_via_webhook(monkeypatch):
    failures.set_store(FakeFailureStore())
    # the feed channel for "leads" is "📈leads" per config.DISCORD_FEED_CHANNELS
    monkeypatch.setattr(discord_feed, "_load_hooks",
                        lambda: {"📈leads": "https://discord.com/api/webhooks/9/xyz"})
    captured = {}
    def post(url, payload):
        captured["url"], captured["payload"] = url, payload
        return True
    ok = discord_feed.feed_lead(
        {"name": "Acme Plumbing", "category": "plumber", "region": "Utah", "phone": "555-1234"},
        http_post=post)
    assert ok is True
    embed = captured["payload"]["embeds"][0]
    assert "Acme Plumbing" in embed["title"]
    field_names = {f["name"] for f in embed["fields"]}
    assert {"category", "region", "phone"} <= field_names


def test_feed_fire_and_audit(monkeypatch):
    failures.set_store(FakeFailureStore())
    monkeypatch.setattr(discord_feed, "_load_hooks", lambda: {
        "🔥fires": "https://discord.com/api/webhooks/1/a",
        "🛡️audit-ledger": "https://discord.com/api/webhooks/2/b"})
    calls = []
    def post(url, payload):
        calls.append((url, payload)); return True
    assert discord_feed.feed_fire({"engine": "Apex", "symbol": "ES", "side": "long",
                                   "entry": 5000, "target": 5020, "stop": 4990}, http_post=post)
    assert discord_feed.feed_audit("daemon", "process_died", "pid 42 gone", http_post=post)
    assert len(calls) == 2
    assert "Apex" in calls[0][1]["embeds"][0]["title"]
