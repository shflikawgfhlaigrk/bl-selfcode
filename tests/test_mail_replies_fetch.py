"""The IMAP fetch itself, proven off the network via an injected connection —
plus classify() precision: bounce detection must match the DSN local part
EXACTLY, never a substring (a pitched postmaster@/``*postmaster*`` prospect was
misfiled as a bounce, silently eating their reply)."""
from __future__ import annotations

import imaplib

import pytest

from utah import mail_replies


PITCH = "thachhutlaundry@gmail.com"


# --- classify precision ---------------------------------------------------------------


def test_pitched_postmaster_address_is_a_reply_not_a_bounce():
    """We pitched postmaster@their-domain; their answer must page, not be
    swallowed as a DSN."""
    pitched = {"postmaster@thachhut.com"}
    assert mail_replies.classify("postmaster@thachhut.com", pitched) == "reply"


def test_bounce_match_is_exact_local_part_not_substring():
    pitched: set[str] = set()
    # a human whose address merely CONTAINS 'postmaster' is vendor noise, not a DSN
    assert mail_replies.classify("thepostmastery@gmail.com", pitched) == "other"
    assert mail_replies.classify("mailer-daemon.fan@gmail.com", pitched) == "other"
    # the real DSN senders still classify as bounces
    assert mail_replies.classify("postmaster@outlook.com", pitched) == "bounce"
    assert mail_replies.classify("MAILER-DAEMON@googlemail.com", pitched) == "bounce"


def test_classify_still_handles_strangers_and_empty():
    assert mail_replies.classify("stranger@x.com", {PITCH}) == "other"
    assert mail_replies.classify("", {PITCH}) == "other"
    assert mail_replies.classify(None, {PITCH}) == "other"


# --- the fetch, off the network --------------------------------------------------------


def _raw(sender=f"Th Laundry <{PITCH}>", subject="Re: Quick idea",
         message_id="<m1@x>", body="yes, build my site"):
    lines = [f"From: {sender}", f"Subject: {subject}"]
    if message_id is not None:
        lines.append(f"Message-ID: {message_id}")
    lines += ["Content-Type: text/plain; charset=utf-8", "", body]
    return "\r\n".join(lines).encode()


class FakeIMAP:
    """Just enough of imaplib.IMAP4_SSL for _imap_fetch: login/select/uid/logout."""

    def __init__(self, search=("OK", [b""]), messages=None, login_exc=None):
        self.search_resp = search
        self.messages = messages or {}          # uid int -> raw rfc822 bytes
        self.login_exc = login_exc
        self.calls: list[tuple] = []
        self.logged_out = False

    def login(self, user, password):
        self.calls.append(("login", user, password))
        if self.login_exc is not None:
            raise self.login_exc
        return "OK", [b"ok"]

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"1"]

    def uid(self, cmd, *args):
        self.calls.append(("uid", cmd, *args))
        if cmd == "search":
            return self.search_resp
        assert cmd == "fetch"
        raw = self.messages.get(int(args[0]))
        if raw is None:
            return "NO", [None]
        return "OK", [(b"1 (BODY[] {n}", raw)]

    def logout(self):
        self.logged_out = True
        return "BYE", [b""]


ACCOUNT = {"from": "me@gmail.com", "app_password": "secret"}


def test_fetch_parses_new_messages_and_skips_already_seen_uid():
    """Gmail re-returns the last seen UID even when nothing is new — it must be
    skipped; the genuinely new message comes back fully parsed."""
    fake = FakeIMAP(search=("OK", [b"5 6"]), messages={6: _raw()})
    out = mail_replies._imap_fetch(ACCOUNT, 5, conn_factory=lambda host: fake)
    assert out == [{"uid": 6, "sender": PITCH, "subject": "Re: Quick idea",
                    "message_id": "<m1@x>", "snippet": "yes, build my site"}]
    assert ("login", "me@gmail.com", "secret") in fake.calls
    assert ("select", "INBOX", True) in fake.calls       # readonly: poll never mutates
    assert ("uid", "search", None, "UID 6:*") in fake.calls
    assert fake.logged_out


def test_first_poll_is_bounded_to_a_recent_window_not_the_whole_mailbox():
    fake = FakeIMAP()
    mail_replies._imap_fetch(ACCOUNT, 0, conn_factory=lambda host: fake)
    criteria = [c[3] for c in fake.calls if c[:2] == ("uid", "search")]
    assert len(criteria) == 1 and criteria[0].startswith("SINCE ")


def test_search_failure_returns_empty_not_crash():
    fake = FakeIMAP(search=("NO", [b""]))
    assert mail_replies._imap_fetch(ACCOUNT, 0, conn_factory=lambda host: fake) == []
    assert fake.logged_out


def test_unfetchable_uid_is_skipped_and_the_rest_still_parse():
    fake = FakeIMAP(search=("OK", [b"7 8"]), messages={8: _raw(message_id="<m8@x>")})
    out = mail_replies._imap_fetch(ACCOUNT, 0, conn_factory=lambda host: fake)
    assert [m["uid"] for m in out] == [8]


def test_missing_message_id_falls_back_to_account_scoped_uid_key():
    """The idempotency key must NEVER be empty — a blank message_id would collide
    every keyless message into one ledger row."""
    fake = FakeIMAP(search=("OK", [b"9"]), messages={9: _raw(message_id=None)})
    out = mail_replies._imap_fetch(ACCOUNT, 0, conn_factory=lambda host: fake)
    assert out[0]["message_id"] == "uid:me@gmail.com:9"


def test_multipart_takes_the_plain_text_part_and_truncates_to_1000():
    body_text = "REPLY-BODY " * 200                       # > 1000 chars
    raw = (
        b"From: " + PITCH.encode() + b"\r\n"
        b"Subject: Re: site\r\n"
        b"Message-ID: <mp@x>\r\n"
        b'Content-Type: multipart/alternative; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<b>html junk</b>\r\n"
        b"--B\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
        + body_text.encode() + b"\r\n--B--\r\n"
    )
    fake = FakeIMAP(search=("OK", [b"3"]), messages={3: raw})
    out = mail_replies._imap_fetch(ACCOUNT, 0, conn_factory=lambda host: fake)
    snippet = out[0]["snippet"]
    assert snippet.startswith("REPLY-BODY") and "html junk" not in snippet
    assert len(snippet) == 1000


def test_logout_happens_even_when_login_explodes():
    """The finally must release the connection on every path — a creds failure
    propagates (poll documents it as imap_failed) but never leaks the socket."""
    fake = FakeIMAP(login_exc=imaplib.IMAP4.error("AUTHENTICATIONFAILED"))
    with pytest.raises(imaplib.IMAP4.error):
        mail_replies._imap_fetch(ACCOUNT, 0, conn_factory=lambda host: fake)
    assert fake.logged_out


def test_imap_host_defaults_to_gmail_and_honors_override():
    hosts: list[str] = []

    def factory(host):
        hosts.append(host)
        return FakeIMAP()

    mail_replies._imap_fetch(ACCOUNT, 0, conn_factory=factory)
    mail_replies._imap_fetch({**ACCOUNT, "imap_host": "imap.fastmail.com"}, 0,
                             conn_factory=factory)
    assert hosts == ["imap.gmail.com", "imap.fastmail.com"]
