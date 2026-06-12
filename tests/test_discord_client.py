"""Discord REST client mechanics — back-off, retries, honest errors, bounded calls,
webhook persistence, creds resolution. Complements test_discord.py (provisioner)."""
from __future__ import annotations

import json

import pytest

from utah import failures
from utah.integrations import discord
from tests.fakes import FakeFailureStore


class Resp:
    def __init__(self, status, payload, text=None):
        self.status_code = status
        self._payload = payload
        self.headers = {}
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        return self._payload


class SeqSession:
    """Replays a scripted response sequence and records every call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list = []

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "json": json, "timeout": timeout})
        return self.responses.pop(0)


def _client(responses):
    return discord.Discord("TOK", session=SeqSession(responses))


# --- request(): bounds, back-off, honest errors --------------------------------
def test_every_call_is_time_bounded_and_authed():
    dc = _client([Resp(200, {"id": "1"})])
    dc.request("GET", "/users/@me")
    call = dc._session.calls[0]
    assert call["timeout"] and 0 < call["timeout"] <= 60
    assert call["headers"]["Authorization"] == "Bot TOK"
    assert call["headers"]["User-Agent"] == discord.USER_AGENT


def test_429_honors_retry_after_then_retries(monkeypatch):
    naps: list = []
    monkeypatch.setattr(discord.time, "sleep", lambda s: naps.append(s))
    dc = _client([Resp(429, {"retry_after": 2.5}), Resp(200, {"ok": 1})])
    assert dc.request("GET", "/x") == {"ok": 1}
    assert naps and abs(naps[0] - 2.6) < 0.01               # min(2.5, 10) + 0.1


def test_429_retry_after_is_capped(monkeypatch):
    """A hostile/buggy retry_after of an hour must not park the daemon."""
    naps: list = []
    monkeypatch.setattr(discord.time, "sleep", lambda s: naps.append(s))
    dc = _client([Resp(429, {"retry_after": 3600}), Resp(200, {})])
    dc.request("GET", "/x")
    assert naps[0] <= 10.2


def test_transient_5xx_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr(discord.time, "sleep", lambda s: None)
    dc = _client([Resp(502, {}), Resp(503, {}), Resp(200, {"ok": 1})])
    assert dc.request("GET", "/x") == {"ok": 1}
    assert len(dc._session.calls) == 3


def test_4xx_raises_discord_error_with_code():
    dc = _client([Resp(403, {"message": "Missing Permissions"})])
    with pytest.raises(discord.DiscordError, match="403"):
        dc.request("POST", "/guilds/G/channels", {"name": "x"})


def test_exhausted_429s_raise(monkeypatch):
    monkeypatch.setattr(discord.time, "sleep", lambda s: None)
    dc = _client([Resp(429, {"retry_after": 0.1})] * 4)
    with pytest.raises(discord.DiscordError, match="exhausted"):
        dc.request("GET", "/x", _tries=4)


def test_204_is_empty_success():
    dc = _client([Resp(204, None, text="")])
    assert dc.request("DELETE", "/channels/1") == {}


def test_error_text_is_truncated_in_the_message():
    dc = _client([Resp(400, {}, text="x" * 5000)])
    with pytest.raises(discord.DiscordError) as exc:
        dc.request("GET", "/x")
    assert len(str(exc.value)) < 1000


def test_send_message_truncates_to_discord_limit():
    dc = _client([Resp(200, {"id": "m1"})])
    dc.send_message("C1", "y" * 9000)
    assert len(dc._session.calls[0]["json"]["content"]) == 2000


# --- webhook post boundary -------------------------------------------------------
def test_post_truncates_and_reports_transport_failure():
    store = FakeFailureStore(); failures.set_store(store)

    def boom(url, payload):
        raise ConnectionError("dns down")

    ok = discord.post("https://discord.com/api/webhooks/1/t", "z" * 9000, http_post=boom)
    assert ok is False
    assert any("webhook_post_error" in row[2] for row in store.rows)


def test_post_falsy_transport_result_is_false():
    failures.set_store(FakeFailureStore())
    assert discord.post("https://discord.com/api/webhooks/1/t", "hi",
                        http_post=lambda url, payload: False) is False


# --- creds + invite resolution ----------------------------------------------------
def test_env_token_fills_in_when_file_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(discord, "SECRET", tmp_path / "absent.json")
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    assert discord.available() is False
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "ENV-TOK")
    assert discord.available() is True
    assert discord._load_creds()["bot_token"] == "ENV-TOK"


def test_invite_url_reads_creds_and_defaults_empty(tmp_path, monkeypatch):
    secret = tmp_path / "discord.json"
    monkeypatch.setattr(discord, "SECRET", secret)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_GUILD_ID", raising=False)
    assert discord.invite_url() == ""
    secret.write_text(json.dumps({"invite_url": " https://discord.gg/utah "}))
    assert discord.invite_url() == "https://discord.gg/utah"


# --- webhook persistence ------------------------------------------------------------
def test_save_webhooks_merges_and_locks_down(tmp_path, monkeypatch):
    path = tmp_path / "discord_webhooks.json"
    monkeypatch.setattr(discord, "WEBHOOKS_FILE", path)
    path.write_text(json.dumps({"old": "https://h/old"}))
    discord.save_webhooks({"webhooks": {"📈leads": "https://h/leads",
                                        "skipped": "(dry-run)", "empty": ""}})
    saved = json.loads(path.read_text())
    assert saved == {"old": "https://h/old", "📈leads": "https://h/leads"}
    assert (path.stat().st_mode & 0o777) == 0o600           # posting creds stay private


def test_save_webhooks_tolerates_garbled_prior_state(tmp_path, monkeypatch):
    path = tmp_path / "discord_webhooks.json"
    monkeypatch.setattr(discord, "WEBHOOKS_FILE", path)
    path.write_text("{nope")
    discord.save_webhooks({"webhooks": {"📈leads": "https://h/leads"}})
    assert json.loads(path.read_text()) == {"📈leads": "https://h/leads"}


def test_save_webhooks_dry_run_writes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "discord_webhooks.json"
    monkeypatch.setattr(discord, "WEBHOOKS_FILE", path)
    discord.save_webhooks(discord.provision(dry_run=True))
    assert not path.exists()


# --- operator surfaces ---------------------------------------------------------------
def test_render_plan_lists_totals_and_channels():
    plan = discord._render_plan(discord.provision(dry_run=True))
    assert "Totals: 9 categories, 34 channels, 6 roles, 19 feed webhooks." in plan
    assert "ask-ace" in plan


def test_cli_status_is_json_and_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(discord, "SECRET", tmp_path / "absent.json")
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    assert discord._main(["status"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["available"] is False and out["secret_exists"] is False
