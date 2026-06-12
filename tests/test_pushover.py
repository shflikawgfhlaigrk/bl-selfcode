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


def test_legacy_token_user_keys_from_file(tmp_path, monkeypatch):
    """Ace/plan secret shape {token, user} normalizes on load."""
    sec = tmp_path / "pushover.json"
    sec.write_text('{"token":"tok","user":"USERKEY"}')
    monkeypatch.setattr(pushover, "SECRET", sec)
    c = pushover._load_creds()
    assert c["api_token"] == "tok" and c["user_key"] == "USERKEY"
    seen = {}
    r = pushover.send("probe", http_post=lambda u, f, timeout=10.0: (seen.update(f), (200, '{"status":1}'))[1])
    assert r["sent"] is True and seen["token"] == "tok" and seen["user"] == "USERKEY"


def test_available_reflects_creds(monkeypatch):
    monkeypatch.setattr(pushover, "_load_creds", lambda: {"api_token": "t", "user_key": "u"})
    assert pushover.available() is True
    monkeypatch.setattr(pushover, "_load_creds", lambda: {"api_token": ""})
    assert pushover.available() is False
    monkeypatch.setattr(pushover, "_load_creds", lambda: None)
    assert pushover.available() is False


def test_e2ee_encrypts_fields_when_key_present(monkeypatch):
    failures.set_store(FakeFailureStore())
    key = "0" * 64
    _creds(monkeypatch, encryption_key=key)
    seen = {}
    pushover.send("hello body", title="Hello", url="http://deck/", url_title="Open",
                  http_post=lambda u, f, timeout=10.0: (seen.update(f), _ok())[1])
    assert seen["encrypted"] == "1"
    assert seen["message"] != "hello body"
    assert seen["title"] != "Hello"
    assert seen["url"] != "http://deck/"
    assert seen["url_title"] != "Open"


def test_e2ee_matches_openssl_reference():
    key = "0123456789abcdef" * 4
    iv = bytes.fromhex("00112233445566778899aabbccddeeff")
    py = pushover.encrypt_field("This has been encrypted", key, iv=iv)
    import subprocess
    sh = f'''
KEY="{key}"
IV=00112233445566778899aabbccddeeff
    CT=$(printf '%s' "This has been encrypted" | gzip -9 -n | \\
  openssl enc -aes-256-cbc -K "$KEY" -iv "$IV" | xxd -p | tr -d '\\n')
HMAC=$(printf '%s%s' "$IV" "$CT" | xxd -r -p | \\
  openssl dgst -sha256 -mac HMAC -macopt hexkey:"$KEY" | awk '{{print $NF}}')
printf '%s%s' "$IV" "$CT" "$HMAC" | xxd -r -p | openssl base64 -A
'''
    ref = subprocess.check_output(["bash", "-lc", sh], text=True).strip()
    assert py == ref
