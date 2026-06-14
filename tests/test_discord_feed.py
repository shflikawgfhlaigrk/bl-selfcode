"""Feed bridge — key→channel→webhook resolution, embed shaping/limits, bus-event
mirroring. Zero network: the transport is injected everywhere."""
from __future__ import annotations

import json

from utah import config, failures
from utah.integrations import discord_feed
from tests.fakes import FakeFailureStore


def _hooks(monkeypatch, mapping):
    monkeypatch.setattr(discord_feed, "_load_hooks", lambda: mapping)


def _capture():
    calls: list = []

    def post(url, payload):
        calls.append((url, payload))
        return True
    return calls, post


# --- resolution ----------------------------------------------------------------
def test_webhook_for_maps_feed_key_through_config(monkeypatch):
    _hooks(monkeypatch, {"📈leads": "https://h/leads", "🔥fires": "https://h/fires"})
    assert discord_feed.webhook_for("leads") == "https://h/leads"
    assert discord_feed.webhook_for("fires") == "https://h/fires"
    assert discord_feed.webhook_for("not-a-feed") is None     # unknown key, no guess
    assert discord_feed.webhook_for("probate") is None        # known key, no hook yet


def test_every_config_feed_key_resolves_to_a_channel():
    """config.DISCORD_FEED_CHANNELS is the single source of truth — every value must
    be reachable back through _KEY_TO_CHANNEL or its feed silently never posts."""
    for channel, key in config.DISCORD_FEED_CHANNELS.items():
        assert discord_feed._KEY_TO_CHANNEL[key] == channel


def test_available_overall_and_per_key(monkeypatch):
    _hooks(monkeypatch, {})
    assert discord_feed.available() is False
    assert discord_feed.available("leads") is False
    _hooks(monkeypatch, {"📈leads": "https://h/leads"})
    assert discord_feed.available() is True
    assert discord_feed.available("leads") is True
    assert discord_feed.available("fires") is False


def test_garbled_hooks_file_reads_as_empty(tmp_path, monkeypatch):
    bad = tmp_path / "discord_webhooks.json"
    bad.write_text("{nope")
    monkeypatch.setattr(discord_feed, "WEBHOOKS", bad)
    assert discord_feed._load_hooks() == {}
    bad.write_text(json.dumps(["not", "a", "dict"]))
    assert discord_feed._load_hooks() == {}


# --- publish: gate, plain message, embed limits ----------------------------------
def test_publish_without_webhook_is_a_gate_not_a_post(monkeypatch):
    failures.set_store(FakeFailureStore())
    _hooks(monkeypatch, {})
    calls, post = _capture()
    assert discord_feed.publish("leads", "hello", http_post=post) is False
    assert calls == []


def test_publish_skips_mock_webhook_without_posting_or_failure(monkeypatch):
    store = FakeFailureStore()
    failures.set_store(store)
    _hooks(monkeypatch, {"📈leads": "https://mock.discord.example/webhooks/1/t"})
    calls, post = _capture()
    assert discord_feed.publish("leads", "hello", http_post=post) is False
    assert calls == []
    assert store.rows == []


def test_publish_plain_content_has_no_embed(monkeypatch):
    _hooks(monkeypatch, {"📈leads": "https://h/leads"})
    calls, post = _capture()
    assert discord_feed.publish("leads", "hello there", http_post=post) is True
    url, payload = calls[0]
    assert url == "https://h/leads"
    assert payload["content"] == "hello there"
    assert "embeds" not in payload


def test_publish_embed_respects_discord_limits(monkeypatch):
    _hooks(monkeypatch, {"📈leads": "https://h/leads"})
    calls, post = _capture()
    fields = {f"k{i}": "v" * 5000 for i in range(40)}
    ok = discord_feed.publish("leads", "d" * 9000, title="t" * 999,
                              fields=fields, http_post=post)
    assert ok is True
    embed = calls[0][1]["embeds"][0]
    assert len(embed["title"]) == 256
    assert len(embed["description"]) == 4000
    assert len(embed["fields"]) == 25
    assert all(len(f["value"]) <= 1024 and len(f["name"]) <= 256 for f in embed["fields"])
    assert embed["color"] == 0x3B82F6                       # the leads brand color


# --- domain helpers ----------------------------------------------------------------
def test_feed_merge_announce_alert_payloads(monkeypatch):
    _hooks(monkeypatch, {"✅merges": "https://h/m", "📣announcements": "https://h/a",
                          "🚨alerts": "https://h/c"})
    calls, post = _capture()
    assert discord_feed.feed_merge("selfcode/abc", utility=0.91, tier="A", http_post=post)
    assert discord_feed.announce("shipped the deck", http_post=post)
    assert discord_feed.alert("daemon down", http_post=post)
    merge, announce, alert = calls
    fields = {f["name"]: f["value"] for f in merge[1]["embeds"][0]["fields"]}
    assert fields["branch"] == "selfcode/abc" and fields["tier"] == "A"
    assert announce[1]["content"] == "shipped the deck"
    assert "Critical" in alert[1]["embeds"][0]["title"]


# --- bus-event mirroring (the ledger's _emit path) -----------------------------------
def test_mirror_routes_each_domain(monkeypatch):
    _hooks(monkeypatch, {"📈leads": "https://h/l", "⚖️probate": "https://h/p",
                          "📨outreach": "https://h/o", "🔥fires": "https://h/f"})
    calls, post = _capture()
    assert discord_feed.mirror("leads", {"name": "Acme", "region": "GA"}, http_post=post)
    assert discord_feed.mirror("probate", {"case_name": "Estate of Smith",
                                           "county": "Coweta"}, http_post=post)
    assert discord_feed.mirror("outreach", {"recipient": "bob@x.com",
                                            "campaign": "smb"}, http_post=post)
    assert discord_feed.mirror("trading", {"engine": "Apex", "direction": "long",
                                           "symbol": "ES"}, http_post=post)
    titles = [c[1]["embeds"][0]["title"] for c in calls]
    assert "Acme" in titles[0]
    assert "Estate of Smith" in titles[1]
    assert "bob@x.com" in titles[2]
    assert "Apex" in titles[3]


def test_mirror_unknown_channel_is_false_never_a_guess(monkeypatch):
    _hooks(monkeypatch, {"📈leads": "https://h/l"})
    calls, post = _capture()
    assert discord_feed.mirror("not-a-domain", {"x": 1}, http_post=post) is False
    assert calls == []
