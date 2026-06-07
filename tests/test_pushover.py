"""Pushover transport — phone push. Honest gate (no creds → gated, never faked), real
priority/target mapping, emergency retry/expire, and never-raises on a transport blow-up.
The HTTP poster is injected so the whole stack is proven with zero network."""
from __future__ import annotations

from utah import config, failures
from utah.integrations import pushover
from tests.fakes import FakeFailureStore


def _creds(monkeypatch, **over):
    c = {"api_token": "tok", "user_key": "USERKEY", "group_key": "GROUPKEY",
         "default_target": "user"}
    c.update(over)
    monkeypatch.setattr(pushover, "_load_creds", lambda: c)
    return c


def _ok(*_a, **_k):
    return 200, '{"status":1,"request":"abc"}'


def test_gated_without_creds(monkeypatch):
    store = FakeFailureStore(); failures.set_store(store)
    monkeypatch.setattr(pushover, "_load_creds", lambda: None)
    r = pushover.send("hi")
    assert r["sent"] is False and r["gated"] is True
    assert any("gated" in row[2] for row in store.rows)   # gate documented


def test_disabled_master_switch_is_a_noop(monkeypatch):
    failures.set_store(FakeFailureStore())
    monkeypatch.setattr(config, "PUSHOVER_ENABLED", False)
    r = pushover.send("hi", http_post=_ok)
    assert r["sent"] is False and r["gated"] is True and r["reason"] == "disabled"


def test_sends_with_injected_transport(monkeypatch):
    failures.set_store(FakeFailureStore())
    _creds(monkeypatch)
    seen = {}
    def post(url, fields, timeout=10.0):
        seen["url"], seen["fields"] = url, fields
        return _ok()
    r = pushover.send("body", title="T", priority=0, http_post=post)
    assert r["sent"] is True and r["gated"] is False
    assert seen["url"] == pushover.API_URL
    assert seen["fields"]["message"] == "body" and seen["fields"]["title"] == "T"
    assert seen["fields"]["user"] == "USERKEY" and seen["fields"]["token"] == "tok"


def test_target_group_resolves_group_key(monkeypatch):
    failures.set_store(FakeFailureStore())
    _creds(monkeypatch)
    seen = {}
    pushover.send("x", target="group", http_post=lambda u, f, timeout=10.0: (seen.update(f), _ok())[1])
    assert seen["user"] == "GROUPKEY"


def test_emergency_priority_adds_retry_and_expire(monkeypatch):
    failures.set_store(FakeFailureStore())
    _creds(monkeypatch)
    seen = {}
    pushover.send("urgent", priority=2,
                  http_post=lambda u, f, timeout=10.0: (seen.update(f), _ok())[1])
    assert seen["priority"] == 2
    assert seen["retry"] == config.PUSHOVER_EMERGENCY_RETRY
    assert seen["expire"] == config.PUSHOVER_EMERGENCY_EXPIRE


def test_url_and_url_title_passed(monkeypatch):
    failures.set_store(FakeFailureStore())
    _creds(monkeypatch)
    seen = {}
    pushover.send("b", url="http://deck/", url_title="Open",
                  http_post=lambda u, f, timeout=10.0: (seen.update(f), _ok())[1])
    assert seen["url"] == "http://deck/" and seen["url_title"] == "Open"


def test_non_ok_response_is_documented_not_faked(monkeypatch):
    store = FakeFailureStore(); failures.set_store(store)
    _creds(monkeypatch)
    r = pushover.send("x", http_post=lambda u, f, timeout=10.0: (400, '{"status":0,"errors":["bad"]}'))
    assert r["sent"] is False and r["gated"] is False
    assert any("send_failed" in row[2] for row in store.rows)


def test_transport_exception_never_raises(monkeypatch):
    store = FakeFailureStore(); failures.set_store(store)
    _creds(monkeypatch)
    def boom(*_a, **_k):
        raise OSError("network down")
    r = pushover.send("x", http_post=boom)
    assert r["sent"] is False and "network down" in r["error"]
    assert any("send_failed" in row[2] for row in store.rows)


def test_available_reflects_creds(monkeypatch):
    monkeypatch.setattr(pushover, "_load_creds", lambda: {"api_token": "t", "user_key": "u"})
    assert pushover.available() is True
    monkeypatch.setattr(pushover, "_load_creds", lambda: {"api_token": ""})
    assert pushover.available() is False
    monkeypatch.setattr(pushover, "_load_creds", lambda: None)
    assert pushover.available() is False
