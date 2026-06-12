"""mail_replies never-raises boundary, proven hostile-path by hostile-path.

The 15-minute cron must survive: a ledger that dies mid-write (and the unrecorded
reply must NOT be skipped forever — the UID cursor may only advance past a message
once it is recorded), a pager that throws, a bounce-flip that fails, an unwritable
state file, and one dead inbox among many. Plus the pure header helpers
(`_decode` / `_sender_email`) that the IMAP fetch depends on."""
from __future__ import annotations

import json

import pytest

from utah import mail_replies
from utah.product.ledger import LedgerError


PITCH_A = "thachhutlaundry@gmail.com"
PITCH_B = "blueridgecafe@gmail.com"


class FlakyLedger:
    """FakeLedger with failure knobs on each write surface."""

    def __init__(self, pitched=(), fail_record_for=(), fail_mark_bounced=False):
        self._pitched = {p.lower() for p in pitched}
        self.replies: list[tuple] = []
        self.bounced: list[str] = []
        self.seen_ids: set[str] = set()
        self.fail_record_for = set(fail_record_for)   # message_ids that explode
        self.fail_mark_bounced = fail_mark_bounced

    def pitched_recipients(self):
        return set(self._pitched)

    def record_reply(self, sender, subject, kind, message_id, snippet=""):
        if message_id in self.fail_record_for:
            raise LedgerError("postgres write failed")
        if message_id in self.seen_ids:
            return False
        self.seen_ids.add(message_id)
        self.replies.append((sender, subject, kind, message_id))
        return True

    def mark_bounced(self, recipient):
        if self.fail_mark_bounced:
            raise LedgerError("bounce flip failed")
        self.bounced.append(recipient)
        return 1


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(mail_replies, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(
        mail_replies.mail, "accounts",
        lambda: [{"from": "me@gmail.com", "app_password": "x",
                  "smtp_host": "smtp.gmail.com"}],
    )
    return tmp_path


def _msg(uid, sender, subject="Re: Quick idea", message_id=None, snippet=""):
    return {"uid": uid, "sender": sender, "subject": subject,
            "message_id": message_id or f"<m{uid}@x>", "snippet": snippet}


def _recorded(monkeypatch):
    rows: list[tuple] = []
    monkeypatch.setattr(mail_replies.failures, "record",
                        lambda *a, **k: rows.append(a))
    return rows


# --- ledger write failure: never raises, cursor does NOT skip the lost reply -------


def test_ledger_write_failure_never_raises_and_does_not_lose_the_reply(env, monkeypatch):
    """A reply that could not be recorded must be retried next poll: the UID cursor
    stays BEFORE the failed message (advancing past it would silently lose a
    prospect saying 'yes')."""
    rows = _recorded(monkeypatch)
    ledger = FlakyLedger(pitched=[PITCH_A, PITCH_B], fail_record_for={"<m6@x>"})
    msgs = [_msg(5, "ads-noreply@google.com"), _msg(6, PITCH_A), _msg(7, PITCH_B)]
    res = mail_replies.poll(fetch_fn=lambda a, l: msgs, ledger=ledger,
                            alert_fn=lambda *a: None)
    assert res["ok"] is True                       # the poll completed
    assert any(r[1] == "ledger_write_failed" for r in rows)
    state = json.loads(mail_replies.STATE.read_text())
    assert state["me@gmail.com"] == 5              # past the noise, NOT past the reply

    # next poll: ledger healed -> the same messages are re-offered and both record
    ledger.fail_record_for = set()
    res2 = mail_replies.poll(fetch_fn=lambda a, l: [m for m in msgs if m["uid"] > l],
                             ledger=ledger, alert_fn=lambda *a: None)
    assert res2["replies"] == 2
    assert json.loads(mail_replies.STATE.read_text())["me@gmail.com"] == 7


def test_alert_failure_documented_reply_still_recorded(env, monkeypatch):
    """Paging is best-effort: a dead Pushover must not lose the recorded reply
    or kill the poll."""
    rows = _recorded(monkeypatch)

    def explode(*_a, **_k):
        raise RuntimeError("pushover down")

    ledger = FlakyLedger(pitched=[PITCH_A])
    res = mail_replies.poll(fetch_fn=lambda a, l: [_msg(9, PITCH_A)],
                            ledger=ledger, alert_fn=explode)
    assert res["ok"] is True and res["replies"] == 1
    assert ledger.replies and ledger.replies[0][2] == "reply"
    assert any(r[1] == "alert_failed" for r in rows)
    assert json.loads(mail_replies.STATE.read_text())["me@gmail.com"] == 9


def test_mark_bounced_failure_documented_not_fatal(env, monkeypatch):
    rows = _recorded(monkeypatch)
    ledger = FlakyLedger(pitched=[PITCH_A], fail_mark_bounced=True)
    dsn = _msg(11, "mailer-daemon@googlemail.com", subject="Delivery Status",
               snippet=f"550: <{PITCH_A}> rejected")
    res = mail_replies.poll(fetch_fn=lambda a, l: [dsn], ledger=ledger,
                            alert_fn=lambda *a: None)
    assert res["ok"] is True and res["bounces"] == 1
    assert any(r[1] == "bounce_mark_failed" for r in rows)


def test_unwritable_state_file_documented_not_fatal(env, monkeypatch):
    """State persistence failing degrades to re-examining messages next poll
    (message_id idempotency absorbs that) — it must never crash the cron."""
    rows = _recorded(monkeypatch)
    blocker = env / "blocker"
    blocker.write_text("a file where the state DIR should be")
    monkeypatch.setattr(mail_replies, "STATE", blocker / "state.json")
    ledger = FlakyLedger(pitched=[PITCH_A])
    res = mail_replies.poll(fetch_fn=lambda a, l: [_msg(3, PITCH_A)],
                            ledger=ledger, alert_fn=lambda *a: None)
    assert res["ok"] is True and res["replies"] == 1
    assert any(r[1] == "state_write_failed" for r in rows)


def test_one_dead_inbox_does_not_block_the_second_account(env, monkeypatch):
    monkeypatch.setattr(
        mail_replies.mail, "accounts",
        lambda: [{"from": "dead@gmail.com", "app_password": "x", "smtp_host": "s"},
                 {"from": "live@gmail.com", "app_password": "x", "smtp_host": "s"}],
    )
    rows = _recorded(monkeypatch)

    def fetch(account, last):
        if account["from"] == "dead@gmail.com":
            raise OSError("imap down")
        return [_msg(4, PITCH_A)]

    ledger = FlakyLedger(pitched=[PITCH_A])
    res = mail_replies.poll(fetch_fn=fetch, ledger=ledger, alert_fn=lambda *a: None)
    assert res["ok"] is True and res["replies"] == 1
    assert any(r[1] == "imap_failed" and "dead@gmail.com" in r[2] for r in rows)
    state = json.loads(mail_replies.STATE.read_text())
    assert state["live@gmail.com"] == 4 and "dead@gmail.com" not in state


def test_malformed_message_from_fetch_documented_not_fatal(env, monkeypatch):
    """A fetch implementation handing back garbage (non-int uid) must be documented,
    not allowed to kill the cron — the boundary contract is 'never raises'."""
    rows = _recorded(monkeypatch)
    ledger = FlakyLedger(pitched=[PITCH_A])
    res = mail_replies.poll(fetch_fn=lambda a, l: [{"uid": "not-a-number",
                                                    "sender": PITCH_A}],
                            ledger=ledger, alert_fn=lambda *a: None)
    assert res["ok"] is True
    assert any(r[1] == "process_failed" for r in rows)


# --- pure header helpers ------------------------------------------------------------


def test_decode_handles_rfc2047_none_and_garbage():
    encoded = "=?utf-8?b?SGVsbG8gTWljaGFlbA==?="          # "Hello Michael"
    assert mail_replies._decode(encoded) == "Hello Michael"
    assert mail_replies._decode(None) == ""
    assert mail_replies._decode("") == ""
    assert mail_replies._decode("plain subject") == "plain subject"


def test_sender_email_extraction_forms():
    assert mail_replies._sender_email('"Th Laundry" <ThachHutLaundry@Gmail.com>') == PITCH_A
    assert mail_replies._sender_email(PITCH_A) == PITCH_A
    assert mail_replies._sender_email("No Email Here") == "no email here"
    assert mail_replies._sender_email("") == ""


def test_snippet_truncated_to_1000_chars(env):
    ledger = FlakyLedger(pitched=[PITCH_A])
    long_snippet = "y" * 5000
    captured: list[str] = []
    original = ledger.record_reply

    def spy(sender, subject, kind, message_id, snippet=""):
        captured.append(snippet)
        return original(sender, subject, kind, message_id, snippet)

    ledger.record_reply = spy
    mail_replies.poll(fetch_fn=lambda a, l: [_msg(2, PITCH_A, snippet=long_snippet)],
                      ledger=ledger, alert_fn=lambda *a: None)
    assert captured and len(captured[0]) == 1000
