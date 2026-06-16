"""Client sending identity — the app sends AS the client, from the client's own email.

Black Label Leads spec: a client inputs their own email (+ app password); the app then sends
their outreach FROM that inbox, autonomously, with replies returning TO the client (not the
house inbox). This is per-client identity layered on the existing safe send path: same
header-injection guard, same result contract, but its own Reply-To and no house rotation.
"""
from __future__ import annotations

from utah import failures, mail
from tests.fakes import FakeFailureStore


def _client(monkeypatch, tmp_path):
    f = tmp_path / "client_accounts.json"
    monkeypatch.setattr(mail, "CLIENT_ACCOUNTS_FILE", f)
    return f


def test_register_and_read_client_account_roundtrip(monkeypatch, tmp_path):
    _client(monkeypatch, tmp_path)
    r = mail.register_client_account("acme", {"from": "owner@acme.com", "app_password": "x"})
    assert r["ok"] is True
    acct = mail.client_account("acme")
    assert acct["from"] == "owner@acme.com" and acct["app_password"] == "x"


def test_register_requires_from_and_app_password(monkeypatch, tmp_path):
    _client(monkeypatch, tmp_path)
    assert mail.register_client_account("acme", {"from": "", "app_password": "x"})["ok"] is False
    assert mail.register_client_account("acme", {"from": "a@b.com"})["ok"] is False


def test_client_account_none_when_unregistered(monkeypatch, tmp_path):
    _client(monkeypatch, tmp_path)
    assert mail.client_account("nobody") is None


def test_send_as_sends_from_client_with_reply_to_client(monkeypatch):
    failures.set_store(FakeFailureStore())
    captured = {}

    def cap(account, to, subject, body, *, reply_to=None):
        captured.update(account=account, to=to, reply_to=reply_to)
    monkeypatch.setattr(mail, "_send_via", cap)

    acct = {"from": "owner@acme.com", "app_password": "x"}
    r = mail.send_as(acct, "lead@x.com", "Hi", "Body")
    assert r["sent"] is True and r["from"] == "owner@acme.com"
    assert captured["account"]["from"] == "owner@acme.com"
    assert captured["reply_to"] == "owner@acme.com"   # replies return to the CLIENT


def test_send_as_rejects_header_injection(monkeypatch):
    failures.set_store(FakeFailureStore())
    monkeypatch.setattr(mail, "_send_via", lambda *a, **k: None)
    r = mail.send_as({"from": "o@acme.com", "app_password": "x"}, "a@b.com\r\nBcc: evil@x", "S", "B")
    assert r["sent"] is False and "header rejected" in r["error"]


def test_send_as_gated_without_client_creds(monkeypatch):
    failures.set_store(FakeFailureStore())
    r = mail.send_as({"from": "o@acme.com"}, "a@b.com", "S", "B")  # no app_password
    assert r["sent"] is False and r["gated"] is True


def test_send_as_client_uses_registered_account(monkeypatch, tmp_path):
    failures.set_store(FakeFailureStore())
    _client(monkeypatch, tmp_path)
    mail.register_client_account("acme", {"from": "owner@acme.com", "app_password": "x"})
    seen = {}
    monkeypatch.setattr(mail, "_send_via",
                        lambda account, to, s, b, *, reply_to=None: seen.update(frm=account["from"]))
    r = mail.send_as_client("acme", "lead@x.com", "Hi", "Body")
    assert r["sent"] is True and seen["frm"] == "owner@acme.com"


def test_send_as_client_gated_when_not_registered(monkeypatch, tmp_path):
    failures.set_store(FakeFailureStore())
    _client(monkeypatch, tmp_path)
    r = mail.send_as_client("ghost", "a@b.com", "S", "B")
    assert r["sent"] is False and r["gated"] is True and r["reason"] == "client_not_registered"
