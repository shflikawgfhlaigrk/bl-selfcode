"""Mail hardening — the SMTP boundary is BOUNDED (explicit timeout, never an
indefinite hang inside an hourly cron), ``verify`` probes the account pool (not
only the legacy gmail.json), and the rotation's read-modify-write is serialized
across threads/processes so concurrent crons can't lose per-day counts and
oversend past the per-inbox reputation cap."""
from __future__ import annotations

import json
import smtplib
import threading
import time

import pytest

from utah import mail


class _RecordingSMTP:
    """Stands in for smtplib.SMTP_SSL: records constructor args + logins,
    no network. Context-manager shaped like the real class."""

    calls: list[dict] = []

    def __init__(self, host, port, *, context=None, timeout=None):
        type(self).calls.append(
            {"host": host, "port": port, "context": context, "timeout": timeout,
             "logins": [], "sent": []})
        self._rec = type(self).calls[-1]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        self._rec["logins"].append((user, password))

    def send_message(self, msg):
        self._rec["sent"].append(msg)


@pytest.fixture()
def smtp_recorder(monkeypatch):
    _RecordingSMTP.calls = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", _RecordingSMTP)
    return _RecordingSMTP


ACCT = {"from": "a@gmail.com", "app_password": "pw", "smtp_host": "smtp.gmail.com"}


# --- bounded SMTP -----------------------------------------------------------

def test_send_via_passes_explicit_timeout(smtp_recorder):
    mail._send_via(dict(ACCT), "to@x.com", "S", "B")
    assert len(smtp_recorder.calls) == 1
    call = smtp_recorder.calls[0]
    assert call["timeout"] is not None and call["timeout"] > 0   # bounded, never hangs
    assert call["logins"] == [("a@gmail.com", "pw")]
    assert len(call["sent"]) == 1


def test_smtp_timeout_is_env_tunable_and_sane():
    assert mail.SMTP_TIMEOUT > 0
    assert mail.SMTP_TIMEOUT <= 300            # a cron send must never block for minutes


# --- verify probes the POOL, not only gmail.json ----------------------------

def test_verify_probes_first_pool_account_when_gmail_json_absent(
        monkeypatch, tmp_path, smtp_recorder):
    """With only email_accounts.json configured (no legacy gmail.json), verify
    must probe a pool account — the old gmail.json-only probe reported a
    confusing 'No such file' error while sends were actually working."""
    pool = tmp_path / "email_accounts.json"
    pool.write_text(json.dumps([ACCT]))
    monkeypatch.setattr(mail, "ACCOUNTS_FILE", pool)
    monkeypatch.setattr(mail, "GMAIL_CREDS", tmp_path / "absent.json")

    r = mail.verify()
    assert r == {"ok": True, "gated": False}
    assert smtp_recorder.calls[0]["logins"] == [("a@gmail.com", "pw")]
    assert smtp_recorder.calls[0]["timeout"] is not None      # the probe is bounded too


def test_verify_still_reports_real_auth_failure(monkeypatch, tmp_path):
    pool = tmp_path / "email_accounts.json"
    pool.write_text(json.dumps([ACCT]))
    monkeypatch.setattr(mail, "ACCOUNTS_FILE", pool)
    monkeypatch.setattr(mail, "GMAIL_CREDS", tmp_path / "absent.json")

    def bad_login(account):
        raise smtplib.SMTPAuthenticationError(535, b"BadCredentials")

    monkeypatch.setattr(mail, "_login_probe", bad_login)
    r = mail.verify()
    assert r["ok"] is False and r["gated"] is False
    assert "535" in r["error"]


def test_verify_gated_when_pool_and_gmail_both_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(mail, "ACCOUNTS_FILE", tmp_path / "a.json")
    monkeypatch.setattr(mail, "GMAIL_CREDS", tmp_path / "g.json")
    assert mail.verify() == {"ok": False, "gated": True}


# --- rotation state locking --------------------------------------------------

def test_state_lock_excludes_concurrent_holders(monkeypatch, tmp_path):
    """Two threads inside _state_lock never overlap — the read-modify-write of
    counts/cursor is atomic, so parallel crons can't lose a count (a lost count
    = an extra cold send past the reputation cap)."""
    monkeypatch.setattr(mail, "ROTATION_STATE", tmp_path / "rot.json")
    order: list[tuple[str, str]] = []

    def hold(tag):
        with mail._state_lock():
            order.append((tag, "in"))
            time.sleep(0.05)
            order.append((tag, "out"))

    threads = [threading.Thread(target=hold, args=(t,)) for t in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # strict serialization: in/out pairs never interleave
    assert order[0][0] == order[1][0] and order[2][0] == order[3][0]
    assert order[0][1] == "in" and order[1][1] == "out"


def test_concurrent_sends_never_lose_counts_or_oversend(monkeypatch, tmp_path):
    """N parallel senders: every successful send is counted on disk, and the
    per-inbox daily cap holds exactly (no lost update => no oversend)."""
    pool = tmp_path / "email_accounts.json"
    pool.write_text(json.dumps([
        {"from": "a@gmail.com", "app_password": "pa"},
        {"from": "b@gmail.com", "app_password": "pb"},
    ]))
    monkeypatch.setattr(mail, "ACCOUNTS_FILE", pool)
    monkeypatch.setattr(mail, "ROTATION_STATE", tmp_path / "rot.json")
    monkeypatch.setattr(mail, "PER_ACCOUNT_DAILY", 5)
    monkeypatch.setattr(mail, "_send_via", lambda account, to, s, b: time.sleep(0.002))

    results: list[dict] = []
    lock = threading.Lock()

    def worker():
        r = mail.send("x@y.com", "S", "B")
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(14)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    sent = [r for r in results if r["sent"]]
    gated = [r for r in results if not r["sent"]]
    assert len(sent) == 10 and len(gated) == 4          # cap 5 × 2 inboxes, exactly
    state = json.loads((tmp_path / "rot.json").read_text())
    assert sum(state["counts"].values()) == 10          # every send counted, none lost


def test_state_lock_degrades_unlocked_on_os_failure(monkeypatch, tmp_path):
    """If the lockfile can't be created (read-only dir etc.), the lock is a
    no-op — a send is never blocked by its own safety mechanism."""
    monkeypatch.setattr(mail, "ROTATION_STATE",
                        tmp_path / "missing-parent" / "deep" / "rot.json")

    def boom(*a, **k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(mail.Path, "mkdir", boom, raising=True)
    with mail._state_lock():
        pass                                            # does not raise


# --- malformed inputs stay out of the pool -----------------------------------

def test_accounts_skips_malformed_pool_entries(monkeypatch, tmp_path):
    pool = tmp_path / "email_accounts.json"
    pool.write_text(json.dumps([
        {"from": "good@gmail.com", "app_password": "p"},
        {"from": "no-password@gmail.com"},               # malformed: no app_password
        "just a string",                                 # malformed: not a dict
        {"app_password": "orphan"},                      # malformed: no from
    ]))
    monkeypatch.setattr(mail, "ACCOUNTS_FILE", pool)
    assert [a["from"] for a in mail.accounts()] == ["good@gmail.com"]


def test_load_state_survives_corrupt_file(monkeypatch, tmp_path):
    bad = tmp_path / "rot.json"
    bad.write_text("{not json!!")
    monkeypatch.setattr(mail, "ROTATION_STATE", bad)
    assert mail._load_state() == {}                      # honest empty, never raises
