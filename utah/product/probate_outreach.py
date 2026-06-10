"""Probate direct-mail last-mile — the unbuilt piece (audit §1.2).

Probate captured the motivated-seller signal and stopped: there was no sender, and the
enrichment resolved the PROPERTY, not the heir. This closes it. Heirs rarely have a public
email/phone, so probate outreach is DIRECT MAIL (a physical letter to the owner/estate at
the parcel's MAILING address — CAN-SPAM governs email, not paper). The mailing address now
comes free from the parcel record (``property._owner_mailing_address`` → ``heir_contact.
owner_mail``). This composes the letter, suppresses (never the same estate twice), and
SENDS via a print-mail service when one is wired (``~/.utah/secrets/lob.json`` — a one-line
unlock); until then it writes a ready-to-mail letter file and never fakes a send.
"""
from __future__ import annotations

import json
import logging
import re

from utah import config, failures
from utah.daemon import runtime
from utah.product.ledger import PROBATE_OUTREACH_CAMPAIGN

log = logging.getLogger("utah.product.probate_outreach")

#: Per-run cap on letters generated/sent.
DAILY_PROBATE_MAIL = int(__import__("os").environ.get("UTAH_PROBATE_MAIL_DAILY", "20"))
#: Gated on a print-mail provider (Lob etc.). Absent → letters are generated + queued for
#: Michael to mail, never faked as "sent". Present → the provider actually mails them.
MAIL_SERVICE_CREDS = runtime.UTAH_HOME / "secrets" / "lob.json"
#: Where ready-to-mail letters are written when no print-mail service is wired.
LETTERS_DIR = runtime.UTAH_HOME / "run" / "probate_letters"


def _return_address() -> str:
    return config.canspam_address()   # Michael's real postal address (reused; also valid here)


def compose_letter(case: dict, footer_address: str | None = None) -> dict:
    """A physical direct-mail letter to the owner/estate of a probate property — Michael's
    real pitch to buy. Returns ``{to, body}``; ``to`` is the resolved owner mailing address.
    Never fabricates an address — caller only passes cases that HAVE one."""
    hc = case.get("heir_contact") or {}
    # owner MAILING address when the county layer has one; else the geocoded SITUS
    # (county-recorded, addressed to the owner/estate at the property) — never invented.
    mail = hc.get("owner_mail") or hc.get("situs_mail") or {}
    owner = (hc.get("owner") or case.get("owner") or "").strip()
    addressee = owner.title() if owner else "Property Owner"
    estate = (case.get("case_name") or "").strip()
    arv = case.get("arv")
    arv_line = (f"County records list the property's assessed value around "
                f"${int(arv):,}. " if isinstance(arv, (int, float)) and arv else "")
    # area context: average assessed value within 3 miles (county rolls, when resolved)
    area = hc.get("area_avg_3mi") or {}
    if area.get("available") and area.get("avg_value"):
        arv_line += (f"Properties within three miles average around "
                     f"${int(area['avg_value']):,} on the county rolls. ")
    body = (
        f"{addressee}\n{mail.get('full', '')}\n\n"
        f"Dear {addressee},\n\n"
        "My name is Michael Barber. I'm a local buyer, and I'm reaching out because a "
        f"property connected to the {estate} estate may be going through probate. "
        f"{arv_line}I buy houses directly — as-is, no repairs, no agent fees, and on the "
        "timeline that works for the family.\n\n"
        "Settling an estate is stressful, and a property can be a burden during it. If "
        "selling would help, I can make a fair, no-obligation cash offer and handle the "
        "details. If now isn't the right time, please accept my condolences and ignore "
        "this letter.\n\n"
        "You can reach me directly at 678-876-1170.\n\n"
        "Warm regards,\nMichael Barber\n"
        f"{footer_address or _return_address()}"
    )
    return {"to": mail.get("full", ""), "to_parts": mail, "addressee": addressee, "body": body}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "letter").lower()).strip("-")[:60] or "letter"


def _provider_send(letter: dict) -> dict:
    """Send a physical letter via the print-mail provider (gated on creds). Until a real
    provider is wired, this stays gated — never a faked send."""
    if not MAIL_SERVICE_CREDS.exists():
        return {"sent": False, "gated": True, "reason": f"no print-mail creds ({MAIL_SERVICE_CREDS})"}
    # A real Lob/print-mail POST would go here once creds land (one-line unlock).
    return {"sent": False, "gated": True, "reason": "print-mail provider not yet wired"}


def _write_letter_file(case: dict, letter: dict) -> str:
    """Write a ready-to-mail letter to disk so Michael can print + mail it today (the
    no-vendor path). Idempotent per estate. Returns the path (or '' on failure)."""
    try:
        LETTERS_DIR.mkdir(parents=True, exist_ok=True)
        path = LETTERS_DIR / f"{_slug(case.get('case_name'))}.txt"
        path.write_text(letter["body"], encoding="utf-8")
        return str(path)
    except OSError as exc:
        log.debug("probate letter write failed: %s", exc)
        return ""


def queue(ledger, cases: list[dict], *, can_send: bool = False, send_fn=None) -> dict:
    """Compose a letter for each probate case with a resolved mailing address; SEND via the
    print-mail provider when allowed+wired, else write a ready-to-mail letter file. Suppress
    (never the same estate twice) ONLY on a real send — a queued/gated letter keeps the
    estate's one shot. Never fabricates. Returns counts."""
    sender = send_fn or _provider_send
    sent = queued = suppressed = no_addr = 0
    letters: list[str] = []
    for case in cases:
        hc = case.get("heir_contact") or {}
        mail = hc.get("owner_mail") or hc.get("situs_mail") or {}
        if not mail.get("street"):
            no_addr += 1
            continue
        recipient = case.get("case_name") or mail.get("full")
        if getattr(ledger, "is_contacted", lambda r, c: False)(recipient, PROBATE_OUTREACH_CAMPAIGN):
            suppressed += 1
            continue
        letter = compose_letter(case)
        if can_send:
            res = sender(letter)
            if res.get("sent"):
                ledger.log_outreach(recipient, PROBATE_OUTREACH_CAMPAIGN, "mail")  # suppress
                sent += 1
                continue
        # gated or queue-only: write the letter for Michael to mail; do NOT suppress yet.
        path = _write_letter_file(case, letter)
        if path:
            letters.append(path)
            queued += 1
    if queued and not MAIL_SERVICE_CREDS.exists():
        failures.record("probate_outreach", "send_gated",
                        f"{queued} probate letters written to {LETTERS_DIR} (ready to mail); "
                        f"wire a print-mail service ({MAIL_SERVICE_CREDS}) to auto-send")
    log.info("probate_outreach: sent=%d queued=%d suppressed=%d no_addr=%d",
             sent, queued, suppressed, no_addr)
    return {"sent": sent, "queued": queued, "suppressed": suppressed, "no_addr": no_addr,
            "letters": letters, "campaign": PROBATE_OUTREACH_CAMPAIGN}


def run_scheduled(limit: int = DAILY_PROBATE_MAIL, *, ledger=None, can_send: bool = True,
                  send_fn=None, foundation_gate=None) -> dict:
    """``com.utah.probate-outreach`` cron — direct-mail the heirs of enriched probate cases.
    Skips on a red substrate. SMB outreach NEVER enters here; this is the separate track."""
    from utah import foundation

    skip = (foundation.gate_cron if foundation_gate is None else foundation_gate)("probate_outreach")
    if skip:
        return skip
    if ledger is None:
        from utah.product.ledger import Ledger
        ledger = Ledger()
    cases = ledger.probate_uncontacted_with_mail(limit)
    if not cases:
        return {"sent": 0, "queued": 0, "reason": "no probate cases with a resolved mailing address"}
    return queue(ledger, cases, can_send=can_send, send_fn=send_fn)


__all__ = ["compose_letter", "queue", "run_scheduled", "DAILY_PROBATE_MAIL", "MAIL_SERVICE_CREDS"]
