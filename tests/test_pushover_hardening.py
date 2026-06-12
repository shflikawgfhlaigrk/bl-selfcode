"""Pushover hardening — bounded gzip subprocess in the E2EE path, field clamps at
Pushover's documented limits, key validation, target resolution edges, and creds-file
honesty (garbled file = gate, never a crash)."""
from __future__ import annotations

import json

from utah import failures
from utah.integrations import pushover
from tests.fakes import FakeFailureStore


def _store():
    s = FakeFailureStore()
    failures.set_store(s)
    return s


def _creds(monkeypatch, **over):
    c = {"api_token": "tok", "user_key": "USERKEY", "group_key": "GROUPKEY"}
    c.update(over)
    monkeypatch.setattr(pushover, "_load_creds", lambda: c)
    return c


def _ok(*_a, **_k):
    return 200, '{"status":1}'


# ── bounded E2EE subprocess ───────────────────────────────────────────────────

def test_encrypt_field_gzip_subprocess_is_bounded(monkeypatch):
    """The gzip child must carry an explicit timeout — an unbounded subprocess on
    the alert path can hang every page to the phone."""
    seen = {}

    def fake_check_output(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return b"\x1f\x8b__fake_gz__"

    monkeypatch.setattr("subprocess.check_output", fake_check_output)
    out = pushover.encrypt_field("hello", "0" * 64)
    assert out                                        # pipeline completed on fake bytes
    assert seen["argv"][0] == "gzip"
    assert seen["kw"].get("timeout") and seen["kw"]["timeout"] > 0


# ── clamps (Pushover hard limits) ─────────────────────────────────────────────

def test_message_clamped_to_1024_and_empty_becomes_placeholder(monkeypatch):
    _store(); _creds(monkeypatch)
    seen = {}
    pushover.send("x" * 5000, http_post=lambda u, f, timeout=10.0: (seen.update(f), _ok())[1])
    assert len(seen["message"]) == 1024
    pushover.send("", http_post=lambda u, f, timeout=10.0: (seen.update(f), _ok())[1])
    assert seen["message"] == "(empty)"


def test_title_url_and_url_title_clamped(monkeypatch):
    _store(); _creds(monkeypatch)
    seen = {}
    pushover.send("m", title="t" * 999, url="u" * 999, url_title="v" * 999,
                  http_post=lambda u, f, timeout=10.0: (seen.update(f), _ok())[1])
    assert len(seen["title"]) == 250
    assert len(seen["url"]) == 512 and len(seen["url_title"]) == 100


# ── target resolution edges ───────────────────────────────────────────────────

def test_literal_target_key_passes_through(monkeypatch):
    _store(); _creds(monkeypatch)
    seen = {}
    pushover.send("m", target="gLITERALKEY123",
                  http_post=lambda u, f, timeout=10.0: (seen.update(f), _ok())[1])
    assert seen["user"] == "gLITERALKEY123"


def test_group_target_degrades_to_user_key_when_group_missing(monkeypatch):
    _store(); _creds(monkeypatch, group_key=None)
    seen = {}
    r = pushover.send("m", target="group",
                      http_post=lambda u, f, timeout=10.0: (seen.update(f), _ok())[1])
    assert r["sent"] is True and seen["user"] == "USERKEY"


# ── encryption key validation ─────────────────────────────────────────────────

def test_invalid_encryption_key_sends_plaintext_not_garbage(monkeypatch):
    """A short or non-hex key can't encrypt — better a readable push than a phone
    full of 'error decrypting' noise."""
    _store()
    seen = {}
    for bad in ("deadbeef", "z" * 64):
        _creds(monkeypatch, encryption_key=bad)
        pushover.send("readable", http_post=lambda u, f, timeout=10.0: (seen.update(f), _ok())[1])
        assert seen["message"] == "readable" and "encrypted" not in seen


def test_e2ee_alias_keys_normalize(monkeypatch):
    sec_raw = {"token": "t", "user": "u", "e2ee_key": "a" * 64}
    assert pushover._normalize_creds(sec_raw)["encryption_key"] == "a" * 64
    assert pushover._normalize_creds({"e2e_key": "b" * 64})["encryption_key"] == "b" * 64


# ── creds-file honesty ────────────────────────────────────────────────────────

def test_garbled_creds_file_is_a_gate_not_a_crash(tmp_path, monkeypatch):
    sec = tmp_path / "pushover.json"
    sec.write_text("{definitely not json")
    monkeypatch.setattr(pushover, "SECRET", sec)
    assert pushover._load_creds() is None
    assert pushover.available() is False
    store = _store()
    r = pushover.send("hi")
    assert r["sent"] is False and r["gated"] is True
    assert any(row[2] == "gated" for row in store.rows)


def test_non_dict_creds_file_is_a_gate(tmp_path, monkeypatch):
    sec = tmp_path / "pushover.json"
    sec.write_text(json.dumps(["a", "list"]))
    monkeypatch.setattr(pushover, "SECRET", sec)
    assert pushover._load_creds() is None


# ── transport result honesty ──────────────────────────────────────────────────

def test_unparseable_status_counts_as_failure(monkeypatch):
    store = _store(); _creds(monkeypatch)
    r = pushover.send("m", http_post=lambda u, f, timeout=10.0: ("weird", "<html>"))
    assert r["sent"] is False and r["gated"] is False
    assert any(row[2] == "send_failed" for row in store.rows)


def test_target_key_is_redacted_in_result(monkeypatch):
    _store(); _creds(monkeypatch, user_key="uSECRETSECRETKEY")
    r = pushover.send("m", http_post=_ok)
    assert r["sent"] is True
    assert "SECRETKEY" not in r["target"]            # never leak the full key upstream
