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
import os
import re

from utah import config, failures, mail

log = logging.getLogger("utah.product.outreach")

#: Conservative daily cold-email cap — protects the sending domain's deliverability and
#: reputation (sends go through a personal Gmail). Raise via UTAH_OUTREACH_DAILY once warm.
DAILY_OUTREACH = int(os.environ.get("UTAH_OUTREACH_DAILY", "8"))
DEFAULT_CAMPAIGN = "smb_no_website"


def default_footer() -> dict:
    """CAN-SPAM footer — address from ``UTAH_CANSPAM_ADDRESS`` or ``secrets/business.json``."""
    return {"address": config.canspam_address(), "unsubscribe": "Reply STOP to opt out."}


#: Back-compat alias; prefer :func:`default_footer` (reads live creds).
DEFAULT_FOOTER = default_footer()

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
    f = footer or default_footer()
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


def _footer_is_real(footer: dict | None) -> bool:
    """True only when the CAN-SPAM physical address is a real business input — not the
    placeholder. Sending with the placeholder address is a non-compliant email that burns
    the prospect's one shot, so a send is refused until this is real (the module contract).
    Creds are gated in mail.send; the physical address is gated here."""
    addr = ((footer or default_footer()).get("address") or "").strip()
    return config._canspam_is_real(addr)  # noqa: SLF001 — shared gate with config.canspam_address


def queue(ledger, campaign: str, leads: list[dict], footer: dict | None = None,
          can_send: bool = False, send_fn=None) -> dict:
    """Compose + lint + suppression-queue each lead, and (when allowed) SEND. The send
    is gated: with ``can_send=False`` nothing is sent and the gate is documented. With
    ``can_send=True`` an EMAIL lead is sent via ``mail.send`` (real SMTP if creds are
    present, else it records its own gate and stays queued — never faked). SMS has no
    provider yet, so it always stays queued. ``send_fn`` is injectable for tests.
    ``recipient`` for suppression is the email or phone."""
    sender = send_fn or mail.send
    # Address gate: never SEND with a placeholder CAN-SPAM physical address — that is a
    # non-compliant email that burns the prospect. Refuse, document it, and fall through to
    # queue-only (the lead stays, never sent). (Creds are separately gated in mail.send.)
    do_send = can_send and _footer_is_real(footer)
    addr_gated = ""
    if can_send and not do_send:
        addr_gated = ("send refused: CAN-SPAM physical address not configured "
                      "(Michael's business input) — leads stay queued, never sent")
        failures.record("outreach", "send_gated", f"{campaign}: {addr_gated}")
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
        if do_send and channel == "email":
            # SEND-NOW: don't burn the prospect's one shot on a gated/failed send. Check
            # suppression read-only, send, and commit the never-twice row ONLY on success.
            if getattr(ledger, "is_contacted", lambda r, c: False)(recipient, campaign):
                suppressed += 1
                continue
            res = sender(recipient, msg["subject"], msg["body"])
            if res.get("sent"):
                ledger.log_outreach(recipient, campaign, channel)   # commit suppression
                # Record the actual email in the mail ledger so the deck MAIL panel shows it
                # (caller-side: outreach already holds the ledger). Defensive getattr keeps
                # test fakes / minimal ledgers working — same pattern as is_contacted above.
                getattr(ledger, "record_mail", lambda *a, **k: None)(
                    recipient, msg["subject"], status="sent", channel="email")
                queued += 1
                sent += 1
            else:
                failures.record("outreach", "send_failed",
                                f"{recipient}: {res.get('error') or 'gated'}")
        elif ledger.log_outreach(recipient, campaign, channel):
            queued += 1             # queue-only (no creds / SMS): queuing IS the action
        else:
            suppressed += 1         # already contacted for this campaign — never twice

    gated = addr_gated
    if queued and not can_send:
        gated = ("outreach send is gated: needs sending creds (SMS/email) + a real "
                 "CAN-SPAM physical address (Michael's business inputs)")
        failures.record("outreach", "send_gated",
                        f"{campaign}: {queued} queued, 0 sent — {gated}")
    log.info("outreach %s: queued=%d suppressed=%d needs_contact=%d blocked=%d sent=%d",
             campaign, queued, suppressed, needs_contact, blocked, sent)
    return {"campaign": campaign, "queued": queued, "suppressed": suppressed,
            "needs_contact": needs_contact, "blocked": blocked, "sent": sent, "gated": gated}


#: Email domains owned by large corporations — a no-website SMB never has one. Skip them so
#: autonomous outreach never cold-pitches a Fortune-500 service center (e.g. savannahservice@
#: tesla.com leaked into the lead pile as "Tesla Savannah").
_CORP_EMAIL_DOMAINS: frozenset[str] = frozenset({
    "tesla.com", "walmart.com", "mcdonalds.com", "starbucks.com", "amazon.com", "target.com",
    "homedepot.com", "lowes.com", "fedex.com", "ups.com", "att.com", "verizon.com",
    "comcast.com", "cvs.com", "walgreens.com", "costco.com", "google.com", "apple.com",
})


def _is_emailable_prospect(lead: dict) -> bool:
    """A genuine no-website SMB worth cold-emailing: not a national chain, not a corporate
    inbox. Guards autonomous outreach against pitching big brands a 'you have no website' note."""
    from utah.product import leads as leads_mod

    if leads_mod.is_national_chain(lead.get("name") or ""):
        return False
    email = ((lead.get("contact") or {}).get("email") or "").lower()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    return bool(domain) and domain not in _CORP_EMAIL_DOMAINS


def run_scheduled(campaign: str = DEFAULT_CAMPAIGN, limit: int = DAILY_OUTREACH, *,
                  ledger=None, foundation_gate=None, send_fn=None) -> dict:
    """``com.utah.outreach`` cron — the missing DRIVER. Pull uncontacted email-leads, drop
    chains/corporate inboxes, and actually SEND up to *limit* a CAN-SPAM-compliant pitch
    (suppression-checked, never-twice). Measured volume protects deliverability. Gated on a
    green substrate so a red Postgres never masquerades as 'no leads'. Never raises."""
    from utah import foundation

    gate = foundation.gate_cron if foundation_gate is None else foundation_gate
    skip = gate("outreach")
    if skip:
        return skip
    if ledger is None:
        from utah.product.ledger import Ledger
        ledger = Ledger()
    # pull a buffer and filter to real SMB prospects so corporate leads don't starve real ones
    candidates = ledger.uncontacted_email_leads(campaign, max(limit * 4, limit))
    leads = [l for l in candidates if _is_emailable_prospect(l)][:limit]
    if not leads:
        return {"campaign": campaign, "sent": 0, "queued": 0,
                "reason": "no emailable SMB prospects (chains/corporate filtered)"}
    result = queue(ledger, campaign, leads, footer=default_footer(),
                   can_send=True, send_fn=send_fn)
    log.info("outreach run_scheduled: campaign=%s pulled=%d sent=%d",
             campaign, len(leads), result.get("sent", 0))
    return result


__all__ = ["compose", "content_score", "pick_channel", "queue", "default_footer",
           "run_scheduled", "DAILY_OUTREACH", "DEFAULT_CAMPAIGN",
           "DEFAULT_FOOTER", "SPAM_BLOCK_THRESHOLD"]
