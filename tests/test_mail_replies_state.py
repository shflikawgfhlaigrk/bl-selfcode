"""State-file corruption hardening for the reply poller.

The 15-minute cron persists one UID cursor per inbox in a JSON file under
``~/.utah/run``. That file is owned by nobody but us — yet a crashed write, a
disk-full truncation, or a stray hand-edit can leave ANY shape behind. A corrupt
file or value must degrade to a full rescan (message_id idempotency absorbs the
rerun), never crash the poll or page twice."""
from __future__ import annotations

import json

import pytest

from utah import mail_replies


PITCH = "thachhutlaundry@gmail.com"


class FakeLedger:
    def __init__(self, pitched=()):
        self._pitched = {p.lower() for p in pitched}
        self.replies: list[tuple] = []
        self.seen_ids: set[str] = set()

    def pitched_recipients(self):
        return set(self._pitched)

    def record_reply(self, sender, subject, kind, message_id, snippet=""):
        if message_id in self.seen_ids:
            return False
        self.seen_ids.add(message_id)
        self.replies.append((sender, subject, kind, message_id))
        return True

    def mark_bounced(self, recipient):
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


def _msg(uid, sender, message_id=None):
    return {"uid": uid, "sender": sender, "subject": "Re: idea",
            "message_id": message_id or f"<m{uid}@x>", "snippet": ""}


def test_state_file_holding_a_json_list_degrades_to_full_rescan(env):
    """A non-dict state file (truncated/garbled write) must read as 'no cursor',
    not crash on .get — the poll then starts from UID 0 for every inbox."""
    mail_replies.STATE.write_text(json.dumps([1, 2, 3]))
    seen_last: list[int] = []
    ledger = FakeLedger(pitched=[PITCH])
    res = mail_replies.poll(
        fetch_fn=lambda acct, last: seen_last.append(last) or [_msg(7, PITCH)],
        ledger=ledger, alert_fn=lambda *a: None,
    )
    assert res["ok"] is True and res["replies"] == 1
    assert seen_last == [0]
    # and the rescued state is now a proper dict with the advanced cursor
    assert json.loads(mail_replies.STATE.read_text()) == {"me@gmail.com": 7}


def test_corrupt_cursor_value_degrades_to_zero_not_crash(env):
    mail_replies.STATE.write_text(json.dumps({"me@gmail.com": "not-a-number"}))
    seen_last: list[int] = []
    res = mail_replies.poll(
        fetch_fn=lambda acct, last: seen_last.append(last) or [],
        ledger=FakeLedger(), alert_fn=lambda *a: None,
    )
    assert res["ok"] is True
    assert seen_last == [0]


def test_negative_cursor_is_clamped_to_zero(env):
    """A negative cursor would make the IMAP criteria 'UID 0:*' — clamp it."""
    mail_replies.STATE.write_text(json.dumps({"me@gmail.com": -5}))
    seen_last: list[int] = []
    mail_replies.poll(fetch_fn=lambda acct, last: seen_last.append(last) or [],
                      ledger=FakeLedger(), alert_fn=lambda *a: None)
    assert seen_last == [0]


def test_rescan_after_corruption_never_double_pages(env):
    """Corruption forces a rescan; message_id idempotency must still keep the
    page count at one for an already-recorded reply."""
    ledger = FakeLedger(pitched=[PITCH])
    paged: list[tuple] = []
    msg = _msg(9, PITCH, message_id="<stable@x>")
    mail_replies.poll(fetch_fn=lambda a, l: [msg], ledger=ledger,
                      alert_fn=lambda *a: paged.append(a))
    mail_replies.STATE.write_text("{ corrupt json !!")
    res = mail_replies.poll(fetch_fn=lambda a, l: [msg], ledger=ledger,
                            alert_fn=lambda *a: paged.append(a))
    assert res["ok"] is True and res["replies"] == 0
    assert len(paged) == 1 and len(ledger.replies) == 1


def test_run_scheduled_returns_the_poll_result(env, monkeypatch):
    """The cron entrypoint must hand the poll verdict through unchanged (the
    launchd wrapper logs it) — including the honest gate when creds are absent."""
    monkeypatch.setattr(mail_replies.mail, "accounts", lambda: [])
    res = mail_replies.run_scheduled()
    assert res == {"ok": False, "gated": True, "reason": "no mail creds"}
