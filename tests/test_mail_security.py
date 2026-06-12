"""Mail security & robustness hardening.

SMTP header injection (CWE-93): a recipient or subject carrying CR/LF could smuggle
extra headers (Bcc: a whole spam blast) through one EmailMessage — the send path must
REJECT it before any adapter runs, on both the injected-sender and the real-pool path.
Plus: a failed rotation-state save is LOGGED (a silently lost count = an oversend
tomorrow), and ``sends_remaining`` stays honest for capped/uncapped/empty pools.
"""
from __future__ import annotations

import json
import logging

from utah import failures, mail
from tests.fakes import FakeFailureStore


# --- header injection (CWE-93) ------------------------------------------------

def test_send_rejects_crlf_in_recipient_before_any_adapter_runs():
    failures.set_store(FakeFailureStore())
    called = []
    r = mail.send("victim@x.com\r\nBcc: blast@evil.com", "Subj", "Body",
                  send_fn=lambda *a: called.append(a))
    assert r["sent"] is False
    assert called == []                      # the adapter NEVER ran
    assert "header" in r["error"].lower()


def test_send_rejects_newline_in_subject():
    store = FakeFailureStore(); failures.set_store(store)
    called = []
    r = mail.send("a@b.com", "Hello\nBcc: blast@evil.com", "Body",
                  send_fn=lambda *a: called.append(a))
    assert r["sent"] is False and called == []
    assert any("rejected" in row[2] for row in store.rows)   # documented, not silent


def test_send_rejects_injection_on_the_real_pool_path(monkeypatch, tmp_path):
    """The guard must also cover the rotation path (no injected send_fn)."""
    failures.set_store(FakeFailureStore())
    pool = tmp_path / "email_accounts.json"
    pool.write_text(json.dumps([{"from": "a@gmail.com", "app_password": "p"}]))
    monkeypatch.setattr(mail, "ACCOUNTS_FILE", pool)
    monkeypatch.setattr(mail, "ROTATION_STATE", tmp_path / "rot.json")
    sent = []
    monkeypatch.setattr(mail, "_send_via", lambda *a: sent.append(a))
    r = mail.send("x@y.com\rBcc: e@vil.com", "S", "B")
    assert r["sent"] is False and sent == []
    # the rejected attempt must not burn a rotation slot
    assert not (tmp_path / "rot.json").exists()


def test_send_allows_newlines_in_body():
    """Only HEADERS are injectable — a multi-line body is normal email."""
    failures.set_store(FakeFailureStore())
    sent = []
    r = mail.send("a@b.com", "Subj", "line one\nline two",
                  send_fn=lambda to, s, b: sent.append(b))
    assert r["sent"] is True and sent == ["line one\nline two"]


def test_send_rejects_blank_recipient():
    failures.set_store(FakeFailureStore())
    r = mail.send("   ", "Subj", "Body", send_fn=lambda *a: None)
    assert r["sent"] is False


# --- rotation-state save failures are visible ----------------------------------

def test_save_state_failure_is_logged_not_silent(monkeypatch, tmp_path, caplog):
    target = tmp_path / "rot.json"
    monkeypatch.setattr(mail, "ROTATION_STATE", target)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(mail.Path, "write_text", boom, raising=True)
    with caplog.at_level(logging.WARNING, logger="utah.mail"):
        mail._save_state({"cursor": 1})       # must not raise
    assert any("rotation state" in r.message for r in caplog.records)


# --- sends_remaining stays honest ----------------------------------------------

def test_sends_remaining_zero_with_no_accounts(monkeypatch, tmp_path):
    monkeypatch.setattr(mail, "ACCOUNTS_FILE", tmp_path / "a.json")
    monkeypatch.setattr(mail, "GMAIL_CREDS", tmp_path / "g.json")
    assert mail.sends_remaining() == 0


def test_sends_remaining_counts_down_per_account(monkeypatch, tmp_path):
    pool = tmp_path / "email_accounts.json"
    pool.write_text(json.dumps([
        {"from": "a@gmail.com", "app_password": "pa"},
        {"from": "b@gmail.com", "app_password": "pb"},
    ]))
    state = tmp_path / "rot.json"
    state.write_text(json.dumps({"date": mail._today(),
                                 "counts": {"a@gmail.com": 3}, "cursor": 1}))
    monkeypatch.setattr(mail, "ACCOUNTS_FILE", pool)
    monkeypatch.setattr(mail, "ROTATION_STATE", state)
    monkeypatch.setattr(mail, "PER_ACCOUNT_DAILY", 5)
    assert mail.sends_remaining() == (5 - 3) + 5


def test_sends_remaining_uncapped_pool_is_large_but_bounded(monkeypatch, tmp_path):
    """Cap disabled (0): callers do min(limit, sends_remaining()) — the value must be
    big enough to never throttle, yet a real bounded int (not inf)."""
    pool = tmp_path / "email_accounts.json"
    pool.write_text(json.dumps([{"from": "a@gmail.com", "app_password": "pa"}]))
    monkeypatch.setattr(mail, "ACCOUNTS_FILE", pool)
    monkeypatch.setattr(mail, "PER_ACCOUNT_DAILY", 0)
    n = mail.sends_remaining()
    assert isinstance(n, int) and 1000 <= n < 10**6
