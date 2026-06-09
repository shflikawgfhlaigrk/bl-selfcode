"""Sender rotation — cycle outbound across a POOL of sending accounts.

Cold volume must spread across multiple inboxes so no single Gmail spikes (deliverability).
Accounts live in ~/.utah/secrets/email_accounts.json (a list of {from, app_password,
smtp_host}); with that file absent we fall back to the single gmail.json (back-compat).
A persistent on-disk cursor round-robins across accounts EVEN ACROSS the separate hourly
cron processes, so the load stays even over the day.
"""
from __future__ import annotations

import json

from utah import failures, mail
from tests.fakes import FakeFailureStore


def _write(p, obj):
    p.write_text(json.dumps(obj), encoding="utf-8")


def test_accounts_falls_back_to_single_gmail_json(monkeypatch, tmp_path):
    monkeypatch.setattr(mail, "ACCOUNTS_FILE", tmp_path / "absent.json")
    gj = tmp_path / "gmail.json"
    _write(gj, {"from": "solo@gmail.com", "app_password": "p", "smtp_host": "smtp.gmail.com"})
    monkeypatch.setattr(mail, "GMAIL_CREDS", gj)
    accts = mail.accounts()
    assert [a["from"] for a in accts] == ["solo@gmail.com"]


def test_accounts_reads_the_pool_file(monkeypatch, tmp_path):
    pool = tmp_path / "email_accounts.json"
    _write(pool, [
        {"from": "a@gmail.com", "app_password": "pa", "smtp_host": "smtp.gmail.com"},
        {"from": "b@gmail.com", "app_password": "pb", "smtp_host": "smtp.gmail.com"},
    ])
    monkeypatch.setattr(mail, "ACCOUNTS_FILE", pool)
    accts = mail.accounts()
    assert [a["from"] for a in accts] == ["a@gmail.com", "b@gmail.com"]


def test_send_round_robins_across_accounts(monkeypatch, tmp_path):
    failures.set_store(FakeFailureStore())
    pool = tmp_path / "email_accounts.json"
    _write(pool, [
        {"from": "a@gmail.com", "app_password": "pa", "smtp_host": "smtp.gmail.com"},
        {"from": "b@gmail.com", "app_password": "pb", "smtp_host": "smtp.gmail.com"},
    ])
    monkeypatch.setattr(mail, "ACCOUNTS_FILE", pool)
    monkeypatch.setattr(mail, "ROTATION_STATE", tmp_path / "rot.json")

    used: list[str] = []
    monkeypatch.setattr(mail, "_send_via",
                        lambda account, to, s, b: used.append(account["from"]))

    for _ in range(3):
        r = mail.send("x@y.com", "S", "B")
        assert r["sent"] is True
    assert used == ["a@gmail.com", "b@gmail.com", "a@gmail.com"]   # round-robin, wraps


def test_per_inbox_daily_cap_blocks_when_all_capped(monkeypatch, tmp_path):
    """No single inbox may exceed its safe cold daily cap — protects sender reputation.
    With cap=1 and 2 accounts, the 3rd send (both already at cap) is GATED, never sent."""
    failures.set_store(FakeFailureStore())
    pool = tmp_path / "email_accounts.json"
    _write(pool, [
        {"from": "a@gmail.com", "app_password": "pa", "smtp_host": "smtp.gmail.com"},
        {"from": "b@gmail.com", "app_password": "pb", "smtp_host": "smtp.gmail.com"},
    ])
    monkeypatch.setattr(mail, "ACCOUNTS_FILE", pool)
    monkeypatch.setattr(mail, "ROTATION_STATE", tmp_path / "rot.json")
    monkeypatch.setattr(mail, "PER_ACCOUNT_DAILY", 1)
    monkeypatch.setattr(mail, "_today", lambda: "2026-06-09")
    sent: list[str] = []
    monkeypatch.setattr(mail, "_send_via", lambda account, to, s, b: sent.append(account["from"]))

    assert mail.send("x@y.com", "S", "B")["sent"] is True   # a
    assert mail.send("x@y.com", "S", "B")["sent"] is True   # b
    r = mail.send("x@y.com", "S", "B")                       # both capped
    assert r["sent"] is False and r["gated"] is True
    assert sent == ["a@gmail.com", "b@gmail.com"]            # the 3rd never left


def test_per_inbox_cap_resets_on_new_day(monkeypatch, tmp_path):
    failures.set_store(FakeFailureStore())
    pool = tmp_path / "email_accounts.json"
    _write(pool, [{"from": "a@gmail.com", "app_password": "pa", "smtp_host": "smtp.gmail.com"}])
    monkeypatch.setattr(mail, "ACCOUNTS_FILE", pool)
    monkeypatch.setattr(mail, "ROTATION_STATE", tmp_path / "rot.json")
    monkeypatch.setattr(mail, "PER_ACCOUNT_DAILY", 1)
    monkeypatch.setattr(mail, "_send_via", lambda account, to, s, b: None)

    monkeypatch.setattr(mail, "_today", lambda: "2026-06-09")
    assert mail.send("x@y.com", "S", "B")["sent"] is True
    assert mail.send("x@y.com", "S", "B")["gated"] is True   # capped today
    monkeypatch.setattr(mail, "_today", lambda: "2026-06-10")
    assert mail.send("x@y.com", "S", "B")["sent"] is True    # fresh day, cap reset


def test_rotation_cursor_persists_on_disk(monkeypatch, tmp_path):
    """A fresh process (new call, same state file) continues the rotation, not restart."""
    failures.set_store(FakeFailureStore())
    pool = tmp_path / "email_accounts.json"
    _write(pool, [
        {"from": "a@gmail.com", "app_password": "pa", "smtp_host": "smtp.gmail.com"},
        {"from": "b@gmail.com", "app_password": "pb", "smtp_host": "smtp.gmail.com"},
    ])
    state = tmp_path / "rot.json"
    monkeypatch.setattr(mail, "ACCOUNTS_FILE", pool)
    monkeypatch.setattr(mail, "ROTATION_STATE", state)
    monkeypatch.setattr(mail, "_send_via", lambda account, to, s, b: None)

    mail.send("x@y.com", "S", "B")           # uses index 0 -> cursor now 1
    assert json.loads(state.read_text())["cursor"] == 1
    r = mail.send("x@y.com", "S", "B")       # uses index 1 -> cursor now 2
    assert r["from"] == "b@gmail.com"
    assert json.loads(state.read_text())["cursor"] == 2
