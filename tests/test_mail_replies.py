"""Reply detection — the conversion eye. A pitched prospect's reply must page the
phone exactly once; a bounce must flip the ledger; vendor noise must do nothing;
and a lost state file must not double-page (Message-ID idempotency)."""
from __future__ import annotations

import json

import pytest

from utah import mail_replies


class FakeLedger:
    def __init__(self, pitched=()):
        self._pitched = {p.lower() for p in pitched}
        self.replies: list[tuple] = []
        self.bounced: list[str] = []
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
        self.bounced.append(recipient)
        return 1


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(mail_replies, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(
        mail_replies.mail, "accounts",
        lambda: [{"from": "me@gmail.com", "app_password": "x", "smtp_host": "smtp.gmail.com"}],
    )
    return tmp_path


PITCH_TARGET = "thachhutlaundry@gmail.com"


def _msg(uid, sender, subject="Re: Quick idea", message_id=None, snippet=""):
    return {"uid": uid, "sender": sender, "subject": subject,
            "message_id": message_id or f"<m{uid}@x>", "snippet": snippet}


def test_prospect_reply_pages_phone_and_records(env):
    ledger = FakeLedger(pitched=[PITCH_TARGET])
    paged = []
    res = mail_replies.poll(
        fetch_fn=lambda acct, last: [_msg(5, PITCH_TARGET)],
        ledger=ledger,
        alert_fn=lambda s, subj: paged.append((s, subj)) or {"sent": True},
    )
    assert res == {"ok": True, "gated": False, "checked": 1, "replies": 1, "bounces": 0}
    assert paged == [(PITCH_TARGET, "Re: Quick idea")]
    assert ledger.replies[0][2] == "reply"


def test_vendor_noise_ignored(env):
    ledger = FakeLedger(pitched=[PITCH_TARGET])
    paged = []
    res = mail_replies.poll(
        fetch_fn=lambda acct, last: [_msg(6, "ads-noreply@google.com")],
        ledger=ledger, alert_fn=lambda *a: paged.append(a),
    )
    assert res["replies"] == 0 and res["bounces"] == 0
    assert not paged and not ledger.replies


def test_bounce_marks_ledger(env):
    ledger = FakeLedger(pitched=[PITCH_TARGET])
    dsn = _msg(7, "mailer-daemon@googlemail.com", subject="Delivery Status Notification",
               snippet=f"Your message to {PITCH_TARGET} couldn't be delivered. 550")
    res = mail_replies.poll(fetch_fn=lambda acct, last: [dsn], ledger=ledger,
                            alert_fn=lambda *a: pytest.fail("bounce must not page"))
    assert res["bounces"] == 1
    assert ledger.bounced == [PITCH_TARGET]


def test_message_id_idempotent_across_lost_state(env):
    """Same message seen twice (state wiped between polls) records + pages ONCE."""
    ledger = FakeLedger(pitched=[PITCH_TARGET])
    paged = []
    msg = _msg(8, PITCH_TARGET, message_id="<stable@x>")
    mail_replies.poll(fetch_fn=lambda a, l: [msg], ledger=ledger,
                      alert_fn=lambda *a: paged.append(a) or {"sent": True})
    mail_replies.STATE.unlink()  # simulate lost cursor
    mail_replies.poll(fetch_fn=lambda a, l: [msg], ledger=ledger,
                      alert_fn=lambda *a: paged.append(a) or {"sent": True})
    assert len(paged) == 1
    assert len(ledger.replies) == 1


def test_state_advances_per_account(env):
    ledger = FakeLedger(pitched=[PITCH_TARGET])
    seen_last = []
    mail_replies.poll(
        fetch_fn=lambda acct, last: seen_last.append(last) or [_msg(41, "x@y.com")],
        ledger=ledger, alert_fn=lambda *a: None,
    )
    mail_replies.poll(
        fetch_fn=lambda acct, last: seen_last.append(last) or [],
        ledger=ledger, alert_fn=lambda *a: None,
    )
    assert seen_last == [0, 41]
    assert json.loads(mail_replies.STATE.read_text())["me@gmail.com"] == 41


def test_owner_self_mail_never_counts_as_reply(env, monkeypatch):
    """Briefs/spotlights/self-proofs arrive FROM the owner, whose address is also
    in the ledgers as a recipient. Live incident: first poll paged 18 false
    'replies' from Michael to Michael. Owner + pool addresses are never prospects."""
    monkeypatch.setattr(mail_replies.config, "OWNER_EMAIL", "me@gmail.com")
    ledger = FakeLedger(pitched=["me@gmail.com", PITCH_TARGET])  # owner IS in ledger
    paged = []
    res = mail_replies.poll(
        fetch_fn=lambda acct, last: [_msg(9, "me@gmail.com", subject="Utah morning brief")],
        ledger=ledger, alert_fn=lambda *a: paged.append(a),
    )
    assert res["replies"] == 0
    assert not paged and not ledger.replies


def test_no_creds_is_honest_gate(env, monkeypatch):
    monkeypatch.setattr(mail_replies.mail, "accounts", lambda: [])
    res = mail_replies.poll(fetch_fn=lambda a, l: pytest.fail("must not fetch"))
    assert res["gated"] is True


def test_imap_failure_never_raises(env, monkeypatch):
    ledger = FakeLedger()
    recorded = []
    monkeypatch.setattr(mail_replies.failures, "record",
                        lambda *a, **k: recorded.append(a))
    def boom(acct, last):
        raise OSError("imap down")
    res = mail_replies.poll(fetch_fn=boom, ledger=ledger, alert_fn=lambda *a: None)
    assert res["ok"] is True  # the poll completes; the dead inbox is documented
    assert recorded and recorded[0][1] == "imap_failed"


def test_classify_pure():
    pitched = {PITCH_TARGET}
    assert mail_replies.classify("MAILER-DAEMON@googlemail.com", pitched) == "bounce"
    assert mail_replies.classify(PITCH_TARGET.upper(), pitched) == "reply"
    assert mail_replies.classify("stranger@x.com", pitched) == "other"
    assert mail_replies.classify("", pitched) == "other"


def test_bounced_recipient_extraction():
    pitched = {PITCH_TARGET}
    body = f"550 address not found: <{PITCH_TARGET}> rejected; ref support@google.com"
    assert mail_replies.bounced_recipient(body, pitched) == PITCH_TARGET
    assert mail_replies.bounced_recipient("no emails here", pitched) is None
