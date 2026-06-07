"""Mail capability — ready-skeleton, GATED on Michael's Gmail/sending creds. The full send
path is wired (SMTP via app-password at ~/.utah/secrets/gmail.json); with no creds it
documents the gate and returns sent=False (never fakes a send). The sender is injectable so
the wiring is proven without real creds."""
from __future__ import annotations

from utah import failures, mail
from tests.fakes import FakeFailureStore


def test_send_uses_injected_sender_when_provided():
    failures.set_store(FakeFailureStore())
    sent = []
    r = mail.send("a@b.com", "Subj", "Body", send_fn=lambda to, s, b: sent.append((to, s, b)))
    assert r["sent"] is True and r["gated"] is False
    assert sent == [("a@b.com", "Subj", "Body")]


def test_send_is_gated_and_documented_without_creds(monkeypatch):
    store = FakeFailureStore(); failures.set_store(store)
    monkeypatch.setattr(mail, "creds_available", lambda: False)
    r = mail.send("a@b.com", "Subj", "Body")          # no injected sender, no creds
    assert r["sent"] is False and r["gated"] is True
    assert any("gated" in row[2] for row in store.rows)   # documented why it didn't send


def test_send_failure_is_documented():
    store = FakeFailureStore(); failures.set_store(store)
    def boom(to, s, b):
        raise RuntimeError("smtp auth failed")
    r = mail.send("a@b.com", "S", "B", send_fn=boom)
    assert r["sent"] is False and r.get("gated") is False
    assert any("send_failed" in row[2] for row in store.rows)
    assert any("smtp auth failed" in row[3] for row in store.rows)


def test_verify_ok_with_passing_probe():
    r = mail.verify(probe_fn=lambda: None)            # login probe succeeds
    assert r["ok"] is True and r["gated"] is False


def test_verify_reports_auth_failure_without_raising():
    r = mail.verify(probe_fn=lambda: (_ for _ in ()).throw(RuntimeError("535 BadCredentials")))
    assert r["ok"] is False and r.get("gated") is False
    assert "535" in r["error"]                         # surfaced, never raised


def test_verify_gated_without_creds(monkeypatch):
    monkeypatch.setattr(mail, "creds_available", lambda: False)
    r = mail.verify()                                  # no probe, no creds
    assert r["ok"] is False and r["gated"] is True
