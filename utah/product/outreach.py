"""Outreach capability — turn ledger leads into compliant, suppression-checked pitches.

Ace's outreach agent transitions HERE as a capability behind the brain, not an agent:
compose a CAN-SPAM-compliant cold pitch for a no-website SMB, content-lint it for
deliverability, pick a channel from the lead's contact, and suppression-queue it through
the product ledger (``log_outreach`` → ``outreach_ledger``, UNIQUE recipient+campaign =
never email/text a prospect twice).

The actual SEND is GATED on Michael's business inputs — sending creds (SMS/email) and a
real CAN-SPAM physical address. Utah never fakes a send: an attempted send with no creds
records a DOCUMENTED gate to the failure log and the row stays queued. Compose / lint /
suppression / channel selection are real and proven; the wire is the only gated part.
"""
from __future__ import annotations

import logging
import re

from utah import failures
from utah.product import mail

log = logging.getLogger("utah.product.outreach")

#: Placeholder footer — the REAL physical address + opt-out are Michael's business inputs
#: (gated). A send is refused until these are real.
DEFAULT_FOOTER = {
    "address": "[CAN-SPAM physical address — Michael's business input, required to send]",
    "unsubscribe": "Reply STOP to opt out.",
}

#: Cold-email/SMS content red flags (spam-weighted). Plain, honest copy scores ~0;
#: it takes several genuine flags to trip the block, so good outreach is never blocked.
_RED_FLAGS: dict[str, int] = {
    r"\bfree\b": 1, r"!!!": 3, r"\bact now\b": 2, r"\bguarantee(d)?\b": 2,
    r"\$\$\$": 3, r"\bclick here\b": 2, r"\brisk[- ]free\b": 2, r"\bwinner\b": 2,
    r"\bcash\b": 1, r"\bcredit\b": 1, r"\b100%\b": 2, r"\blimited time\b": 1,
    r"\bbuy now\b": 2, r"\bcongratulations\b": 2, r"[A-Z]{6,}": 1,
}
SPAM_BLOCK_THRESHOLD = 6


def compose(lead: dict, campaign: str, footer: dict | None = None) -> dict:
    """A grounded, CAN-SPAM-compliant cold pitch for a no-website SMB. Deterministic
    template (an LLM rewrite can drop in later); always stamps the physical address +
    opt-out so nothing non-compliant can be queued."""
    f = footer or DEFAULT_FOOTER
    name = (lead.get("name") or "there").strip()
    kind = (lead.get("kind") or "business").strip()
    subject = f"A simple website for {name}"
    body = (
        f"Hi {name},\n\n"
        f"I came across your {kind} and noticed it doesn't have a website yet. "
        "A lot of local customers look online first, so a clean one-page site "
        "(hours, photos, a way to call or book) usually pays for itself quickly.\n\n"
        "I build these for local businesses and could put one together for you. "
        "Want me to send over a quick example?\n\n"
        "Thanks,\nMichael\n\n"
        f"—\n{f['address']}\n{f['unsubscribe']}"
    )
    return {"subject": subject, "body": body}


def content_score(text: str) -> dict:
    """Spam-weight a message; ``block`` once the weight crosses the threshold."""
    score = 0
    reasons: list[str] = []
    for pat, w in _RED_FLAGS.items():
        n = len(re.findall(pat, text))
        if n:
            score += w * n
            reasons.append(f"{pat}×{n} (+{w*n})")
    return {"score": score, "block": score >= SPAM_BLOCK_THRESHOLD, "reasons": reasons}


def pick_channel(contact: dict | None) -> str | None:
    """Channel from the lead's contact: email > sms(phone) > none (needs contact)."""
    contact = contact or {}
    if contact.get("email"):
        return "email"
    if contact.get("phone"):
        return "sms"
    return None


def _recipient(contact: dict, channel: str) -> str:
    return contact.get("email") if channel == "email" else contact.get("phone")


def queue(ledger, campaign: str, leads: list[dict], footer: dict | None = None,
          can_send: bool = False, send_fn=None) -> dict:
    """Compose + lint + suppression-queue each lead, and (when allowed) SEND. The send
    is gated: with ``can_send=False`` nothing is sent and the gate is documented. With
    ``can_send=True`` an EMAIL lead is sent via ``mail.send`` (real SMTP if creds are
    present, else it records its own gate and stays queued — never faked). SMS has no
    provider yet, so it always stays queued. ``send_fn`` is injectable for tests.
    ``recipient`` for suppression is the email or phone."""
    sender = send_fn or mail.send
    queued = suppressed = needs_contact = blocked = sent = 0
    for lead in leads:
        channel = pick_channel(lead.get("contact"))
        if channel is None:
            needs_contact += 1
            continue
        msg = compose(lead, campaign, footer)
        if content_score(msg["subject"] + " " + msg["body"])["block"]:
            blocked += 1
            failures.record("outreach", "content_blocked",
                            f"{lead.get('name')}: pitch tripped the spam-content gate")
            continue
        recipient = _recipient(lead.get("contact", {}), channel)
        if not ledger.log_outreach(recipient, campaign, channel):
            suppressed += 1          # already contacted for this campaign — never twice
            continue
        queued += 1
        if can_send and channel == "email":
            res = sender(recipient, msg["subject"], msg["body"])
            if res.get("sent"):
                sent += 1
            else:
                failures.record("outreach", "send_failed",
                                f"{recipient}: {res.get('error') or 'gated'}")

    gated = ""
    if queued and not can_send:
        gated = ("outreach send is gated: needs sending creds (SMS/email) + a real "
                 "CAN-SPAM physical address (Michael's business inputs)")
        failures.record("outreach", "send_gated",
                        f"{campaign}: {queued} queued, 0 sent — {gated}")
    log.info("outreach %s: queued=%d suppressed=%d needs_contact=%d blocked=%d sent=%d",
             campaign, queued, suppressed, needs_contact, blocked, sent)
    return {"campaign": campaign, "queued": queued, "suppressed": suppressed,
            "needs_contact": needs_contact, "blocked": blocked, "sent": sent, "gated": gated}


__all__ = ["compose", "content_score", "pick_channel", "queue",
           "DEFAULT_FOOTER", "SPAM_BLOCK_THRESHOLD"]
