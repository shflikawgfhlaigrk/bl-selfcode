"""Pushover E2EE failure fallback — a gzip blow-up mid-pipeline must NOT escape
send()'s never-raises contract, must NOT ship a half-encrypted payload under the
``encrypted`` flag, and must document the degradation to the failure log."""
from __future__ import annotations

import subprocess

from utah import failures
from utah.integrations import pushover
from tests.fakes import FakeFailureStore


def _store():
    s = FakeFailureStore()
    failures.set_store(s)
    return s


def _creds(monkeypatch, **over):
    c = {"api_token": "tok", "user_key": "USERKEY", "encryption_key": "0" * 64}
    c.update(over)
    monkeypatch.setattr(pushover, "_load_creds", lambda: c)
    return c


def test_gzip_timeout_falls_back_to_plaintext_send(monkeypatch):
    store = _store()
    _creds(monkeypatch)

    def hang(argv, **kw):
        raise subprocess.TimeoutExpired(cmd="gzip", timeout=10)

    monkeypatch.setattr(subprocess, "check_output", hang)
    seen = {}
    r = pushover.send("readable alert", title="T",
                      http_post=lambda u, f, timeout=10.0: (seen.update(f),
                                                            (200, '{"status":1}'))[1])
    assert r["sent"] is True                              # alert still reaches the phone
    assert seen["message"] == "readable alert" and seen["title"] == "T"
    assert "encrypted" not in seen                        # never a lying encrypted flag
    assert any(row[2] == "e2ee_failed" for row in store.rows)


def test_gzip_binary_missing_falls_back_and_documents(monkeypatch):
    store = _store()
    _creds(monkeypatch)

    def gone(argv, **kw):
        raise FileNotFoundError("gzip")

    monkeypatch.setattr(subprocess, "check_output", gone)
    seen = {}
    r = pushover.send("m", http_post=lambda u, f, timeout=10.0: (seen.update(f),
                                                                 (200, '{"status":1}'))[1])
    assert r["sent"] is True and seen["message"] == "m" and "encrypted" not in seen
    assert any(row[2] == "e2ee_failed" for row in store.rows)


def test_apply_e2ee_is_all_or_nothing(monkeypatch):
    """If the SECOND field fails, the first must not ship encrypted either —
    a mixed payload under encrypted=1 renders as garbage on the phone."""
    _store()
    calls = {"n": 0}

    def second_fails(argv, *, input=b"", timeout=None, **kw):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise OSError("gzip died")
        return b"\x1f\x8b_gz_"

    monkeypatch.setattr(subprocess, "check_output", second_fails)
    fields = {"message": "body", "title": "head"}
    out = pushover._apply_e2ee(fields, {"encryption_key": "0" * 64})
    assert out == fields                                  # untouched plaintext, no flag
