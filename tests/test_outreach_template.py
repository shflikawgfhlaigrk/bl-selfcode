"""compose() uses Michael's real pitch (his copy, his price, his phone) + stays compliant.

Michael's template: name + "$700 vs $1700 is absurd" + "sample before you buy" +
phone 678-876-1170. The CAN-SPAM footer (physical address + opt-out) is appended for
legal compliance even though his raw copy omits it, and the pitch must still score below
the spam-content block threshold so it actually sends.
"""
from __future__ import annotations

from utah.product import outreach

_FOOTER = {"address": "28 Dogwood Rd, Example GA 30000", "unsubscribe": "Reply STOP to opt out."}


def test_compose_uses_michaels_pitch_and_contact():
    msg = outreach.compose({"name": "Joe's Plumbing", "kind": "plumber"},
                           outreach.SMB_OUTREACH_CAMPAIGN, _FOOTER)
    body = msg["body"]
    assert "Michael Barber" in body
    assert "678-876-1170" in body
    assert "700" in body            # his target price
    assert "1,700" in body or "1700" in body   # the anchor he contrasts against


def test_compose_personalizes_greeting_and_subject():
    msg = outreach.compose({"name": "Joe's Plumbing", "kind": "plumber"},
                           outreach.SMB_OUTREACH_CAMPAIGN, _FOOTER)
    assert "Joe's Plumbing" in msg["subject"]
    assert msg["body"].startswith("Hello Joe's Plumbing")


def test_compose_keeps_canspam_footer():
    msg = outreach.compose({"name": "Joe's Plumbing", "kind": "plumber"},
                           outreach.SMB_OUTREACH_CAMPAIGN, _FOOTER)
    assert _FOOTER["address"] in msg["body"]
    assert _FOOTER["unsubscribe"] in msg["body"]


def test_michaels_pitch_passes_the_spam_lint():
    msg = outreach.compose({"name": "Joe's Plumbing", "kind": "plumber"},
                           outreach.SMB_OUTREACH_CAMPAIGN, _FOOTER)
    score = outreach.content_score(msg["subject"] + " " + msg["body"])
    assert score["block"] is False, score
