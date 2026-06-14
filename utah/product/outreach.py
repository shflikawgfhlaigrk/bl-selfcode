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

from utah import config, failures, mail, sms
from utah.product.ledger import PROBATE_OUTREACH_CAMPAIGN, SMB_LEAD_SOURCES, SMB_OUTREACH_CAMPAIGN

log = logging.getLogger("utah.product.outreach")

#: Per-RUN cold-outreach cap. The cron fires hourly, so this is the per-HOUR cap;
#: 50/hour × 10 business hours = 500/day (Michael's floor). Env: ``UTAH_OUTREACH_DAILY``.
DAILY_OUTREACH = int(os.environ.get("UTAH_OUTREACH_DAILY", "50"))
DEFAULT_CAMPAIGN = SMB_OUTREACH_CAMPAIGN
#: Channel: ``auto`` (email-first, text-fallback — Michael's directive), ``email``, or
#: ``sms``. Default ``auto`` so a phone-only lead is reached by text when no email exists.
OUTREACH_CHANNEL = os.environ.get("UTAH_OUTREACH_CHANNEL", "auto").strip().lower()


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

#: Michael's business site — goes in every outreach email (proof of work + a real web
#: presence makes a cold pitch credible; same domain as the sending identity).
BUSINESS_SITE = "https://blacklabelbots.com"


#: One short, business-type-aware relevance line, keyed by the lead's ``kind``. Keeps
#: Michael's pitch but makes each email genuinely about THAT business — and, because the
#: opening + subject vary per lead, breaks the identical-bulk-body pattern spam filters
#: flag at volume. Deterministic (no per-lead brain call, no fabrication, test-stable);
#: an optional brain rewrite can drop in later behind this same boundary.
_KIND_HOOKS: dict[str, str] = {
    "restaurant": "Diners almost always look a place up online before they decide where to eat",
    "cafe": "People check online for hours and a menu before they stop in for coffee",
    "bar": "Folks look up the vibe, hours, and events online before they pick a spot",
    "salon": "Most people browse photos and book online before they try a new stylist",
    "beauty": "Clients usually check reviews and photos online before they book",
    "barber": "New clients usually look you up online before they walk in for a cut",
    "trade": "Homeowners almost always check you out online before they let you in the door",
    "handyman": "Homeowners almost always check you out online before they hire for a job",
    "plumber": "When a pipe bursts, people search online and call whoever looks legit first",
    "electrician": "Homeowners search online and hire whoever looks established first",
    "hvac": "When the AC dies, people search online and call the first business they trust",
    "car_repair": "Drivers search online for a mechanic they can trust before they hand over the keys",
    "auto": "Drivers look you up online before they trust you with their car",
    "landscaping": "Homeowners browse online for photos of past work before they hire",
    "cleaning": "People want to see reviews online before they let a cleaner into their home",
    "contractor": "Homeowners vet contractors online before they spend real money",
    "retail": "Shoppers check online for hours, location, and what you carry before stopping by",
    "fitness": "People compare classes and pricing online before they sign up",
    "pet": "Pet owners read reviews online before they trust someone with their animal",
}


def _relevance_hook(kind: str | None) -> str:
    """A type-specific one-liner for *kind* (normalized), or a solid generic fallback."""
    k = (kind or "").strip().lower()
    for key, line in _KIND_HOOKS.items():
        if key in k:
            return line
    return ("A lot of local customers look online first and use a website as a "
            "legitimacy check when they're finding the \"right person\"")


def _subject_variant(name: str, has_name: bool) -> str:
    """A stable subject chosen from a few templates by a hash of the name — so a 500/day
    batch is NOT 500 identical subject lines (a bulk-spam signal). Deterministic per lead."""
    import hashlib

    who = name if has_name else "your business"
    templates = (
        f"A website for {who}",
        f"Quick idea for {who}",
        f"{who} — a website that pays for itself",
    )
    idx = int(hashlib.sha1(name.encode("utf-8", "replace")).hexdigest(), 16) % len(templates)
    return templates[idx]


def compose(lead: dict, campaign: str, footer: dict | None = None) -> dict:
    """Michael's real CAN-SPAM-compliant cold pitch for a no-website SMB. His copy,
    his price ($700 vs the ~$1,700 norm), his contact — personalized to the business
    name AND type (a relevance line keyed by the lead's ``kind`` + a varied subject, so a
    500/day batch isn't identical bodies a spam filter flags); always stamps the physical
    address + opt-out so nothing non-compliant ships."""
    f = footer or default_footer()
    name = (lead.get("name") or "there").strip()
    has_name = bool(name) and name != "there"
    greeting = f"Hello {name}," if has_name else "Hello,"
    subject = _subject_variant(name, has_name)
    hook = _relevance_hook(lead.get("kind"))
    body = (
        f"{greeting}\n\n"
        "My name is Michael Barber — I build websites for small businesses.\n\n"
        "An average website costs around $1,700, which is absurd. I aim for around $700. "
        f"{hook}. It pays for itself quickly.\n\n"
        "I'll show you what it will look like before you buy, so you know it's to your "
        "standard. I could put a sample website together for you, and you could tell me "
        "what customizations you'd like — calendar integrations, Google Maps, and pricing "
        "automations are all easy, and this can be done within a day.\n\n"
        f"You can see examples of my work at {BUSINESS_SITE}.\n\n"
        "Let me know if you're interested.\n\n"
        "Best regards,\n"
        "Michael Barber\n"
        f"{BUSINESS_SITE}\n"
        "678-876-1170\n\n"
        f"—\n{f['address']}\n{f['unsubscribe']}"
    )
    return {"subject": subject, "body": body}


#: Follow-up cadence: (days-after-first-touch, campaign suffix, is-final). Two touches
#: max, ever — a nudge at day 3 and a final note at day 7. Candidates are filtered by
#: the ledger to non-repliers/non-bounces only (reply detection makes this safe).
FOLLOWUP_STAGES: tuple[tuple[float, str, bool], ...] = (
    (3.0, "fu3", False),
    (7.0, "fu7", True),
)
#: Per-run budget for follow-ups inside the hourly cron (cold sends keep their own
#: limit; the mail-side per-inbox daily cap is the true ceiling for both).
FOLLOWUP_RUN_LIMIT = 10


def compose_followup(lead: dict, *, final: bool = False, footer: dict | None = None) -> dict:
    """Short follow-up to a prospect who never answered the first note. Same CAN-SPAM
    footer; deliberately brief (follow-ups convert on politeness, not repetition)."""
    f = footer or default_footer()
    name = (lead.get("name") or "").strip()
    greeting = f"Hello {name}," if name else "Hello,"
    if final:
        subject = f"Last note — website for {name}" if name else "Last note from me"
        middle = (
            "I won't keep nudging — this is my last note. The offer stands: a "
            "professional website for around $700, preview before you pay, live "
            "within a day. If the timing's ever right, my number is below.\n\n"
        )
    else:
        subject = f"Following up — website for {name}" if name else "Following up"
        middle = (
            "Just floating my note back up in case it got buried. I build websites "
            "for small businesses — around $700, you see the design before you pay, "
            "and it can be live within a day.\n\n"
        )
    body = (
        f"{greeting}\n\n{middle}"
        "Best regards,\n"
        "Michael Barber\n"
        f"{BUSINESS_SITE}\n"
        "678-876-1170\n\n"
        f"—\n{f['address']}\n{f['unsubscribe']}"
    )
    return {"subject": subject, "body": body}


def run_followups(*, ledger=None, send_fn=None, limit: int = FOLLOWUP_RUN_LIMIT,
                  now_hour: int | None = None) -> dict:
    """Send due day-3 / day-7 follow-ups (email only). Business-hours gated like every
    send path; suppression is per-stage via the ``<campaign>_<stage>`` ledger key so a
    prospect gets each follow-up at most once, and never after a reply or bounce."""
    if not config.within_business_hours(now_hour):
        return {"sent": 0, "skipped": True, "reason": "outside business hours"}
    footer = default_footer()
    if not _footer_is_real(footer):
        return {"sent": 0, "blocked": True, "reason": "CAN-SPAM footer incomplete"}
    if ledger is None:
        from utah.product.ledger import Ledger
        ledger = Ledger()
    from utah import mail

    sent = 0
    stages: dict[str, int] = {}
    for age_days, suffix, final in FOLLOWUP_STAGES:
        if sent >= limit:
            break
        fu_campaign = f"{SMB_OUTREACH_CAMPAIGN}_{suffix}"
        for cand in ledger.followup_candidates(
                SMB_OUTREACH_CAMPAIGN, fu_campaign, age_days, limit - sent):
            recipient = cand["recipient"]
            if not ledger.log_outreach(recipient, fu_campaign, channel="email"):
                continue   # already got this stage
            msg = compose_followup(cand, final=final, footer=footer)
            res = mail.send(recipient, msg["subject"], msg["body"], send_fn=send_fn)
            if res.get("sent"):
                sent += 1
                stages[suffix] = stages.get(suffix, 0) + 1
                ledger.record_mail(recipient, msg["subject"], status="sent",
                                   sender=res.get("from"))
    return {"sent": sent, "stages": stages}


def compose_sms(lead: dict, campaign: str, footer: dict | None = None) -> dict:
    """Short SMS pitch — TCPA opt-out included; no subject."""
    f = footer or default_footer()
    name = (lead.get("name") or "there").strip()
    body = (
        f"Hi {name} — I noticed your local business doesn't have a website yet. "
        "I build simple sites for trades/handymen (hours, photos, call/book). "
        "Want a quick example? — Michael. "
        f"{f['unsubscribe']}"
    )
    return {"subject": "", "body": body}


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


def pick_channel(contact: dict | None, prefer: str | None = None) -> str | None:
    """Channel from contact; *prefer* (``sms``/``email``) wins when both exist.
    On fallthrough (``auto`` or a prefer the lead can't satisfy) we go EMAIL-FIRST —
    Michael's directive: "if you can't find an email, just send a text." Email is
    the more compliant, free channel, so it's preferred; phone (→ SMS/iMessage)
    is the fallback."""
    contact = contact or {}
    pref = (prefer or OUTREACH_CHANNEL).lower()
    if pref == "sms" and contact.get("phone"):
        return "sms"
    if pref == "email" and contact.get("email"):
        return "email"
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
    return config._canspam_is_complete(addr)  # noqa: SLF001 — shared send gate (street+ZIP)


def _is_smb_lead(lead: dict) -> bool:
    """Only OSM / Google Maps small-business rows — never probate or other sources."""
    if (lead.get("kind") or "").lower() == "probate":
        return False
    return (lead.get("source") or "osm") in SMB_LEAD_SOURCES


def queue(ledger, campaign: str, leads: list[dict], footer: dict | None = None,
          can_send: bool = False, send_fn=None, prefer: str | None = None,
          verify_fn=None) -> dict:
    """Compose + lint + suppression-queue each lead, and (when allowed) SEND. The send
    is gated: with ``can_send=False`` nothing is sent and the gate is documented. With
    ``can_send=True`` an EMAIL lead is sent via ``mail.send`` (real SMTP if creds are
    present, else it records its own gate and stays queued — never faked). SMS has no
    provider yet, so it always stays queued. ``send_fn`` is injectable for tests.
    ``recipient`` for suppression is the email or phone.

    **SMB only:** when *campaign* is :data:`SMB_OUTREACH_CAMPAIGN`, non-SMB rows
    (wrong ``source``, probate-shaped rows) are skipped. Probate heirs use a separate
    campaign/table — see :meth:`Ledger.uncontacted_probate_heirs`."""
    if campaign == PROBATE_OUTREACH_CAMPAIGN:
        failures.record("outreach", "wrong_pipeline",
                        "probate outreach must not use SMB queue — separate track")
        return {"campaign": campaign, "queued": 0, "suppressed": 0, "needs_contact": 0,
                "blocked": 0, "sent": 0,
                "gated": "probate leads use probate_motivated pipeline, not SMB outreach"}
    if campaign == SMB_OUTREACH_CAMPAIGN:
        leads = [l for l in leads if _is_smb_lead(l)]
    sender = send_fn or mail.send
    # Deliverability gate: only ever send to an address that can actually RECEIVE mail.
    # An unverified guess that hard-bounces is precisely what blacklists the sending domain,
    # so a non-deliverable email is counted + skipped, never sent. enrich.verify_email does
    # MX/syntax/role checks (NOT a RCPT-TO probe — that gets the prober blocklisted and breaks
    # on catch-all domains). Safe by default; injectable so tests never touch real DNS.
    if verify_fn is None:
        from utah.product import enrich
        verify_fn = enrich.verify_email
    # Address gate: never SEND with a placeholder CAN-SPAM physical address — that is a
    # non-compliant email that burns the prospect. Refuse, document it, and fall through to
    # queue-only (the lead stays, never sent). (Creds are separately gated in mail.send.)
    do_send = can_send and _footer_is_real(footer)
    addr_gated = ""
    if can_send and not do_send:
        addr_gated = ("send refused: CAN-SPAM physical address not configured "
                      "(Michael's business input) — leads stay queued, never sent")
        failures.record("outreach", "send_gated", f"{campaign}: {addr_gated}")
    n = {"queued": 0, "suppressed": 0, "needs_contact": 0, "blocked": 0,
         "sent": 0, "unverified": 0, "errors": 0}
    for lead in leads:
        try:
            verdict = _queue_one(ledger, campaign, lead, footer, do_send, sender,
                                 prefer, verify_fn)
        except Exception as exc:  # noqa: BLE001 — one bad lead/ledger row/sender must
            # never abort the rest of the batch (the remaining sends are real work);
            # a raise commits NOTHING, so the prospect keeps their one shot.
            n["errors"] += 1
            failures.record("outreach", "lead_failed",
                            f"{lead.get('name') or 'unnamed lead'}: {exc} — batch continued")
            continue
        if verdict == "sent":
            n["sent"] += 1
            n["queued"] += 1        # a landed send is also an actioned (queued) lead
        elif verdict != "send_failed":   # send_failed is documented, lead stays for retry
            n[verdict] += 1

    gated = addr_gated
    if n["queued"] and not can_send:
        gated = ("outreach send is gated: needs sending creds (SMS/email) + a real "
                 "CAN-SPAM physical address (Michael's business inputs)")
        failures.record("outreach", "send_gated",
                        f"{campaign}: {n['queued']} queued, 0 sent — {gated}")
    log.info("outreach %s: queued=%d suppressed=%d needs_contact=%d blocked=%d sent=%d "
             "unverified=%d errors=%d", campaign, n["queued"], n["suppressed"],
             n["needs_contact"], n["blocked"], n["sent"], n["unverified"], n["errors"])
    return {"campaign": campaign, "queued": n["queued"], "suppressed": n["suppressed"],
            "needs_contact": n["needs_contact"], "blocked": n["blocked"], "sent": n["sent"],
            "unverified": n["unverified"], "errors": n["errors"], "gated": gated}


def _queue_one(ledger, campaign: str, lead: dict, footer: dict | None, do_send: bool,
               sender, prefer: str | None, verify_fn) -> str:
    """Compose + lint + (when allowed) SEND one lead. Returns the counter to bump:
    ``needs_contact | blocked | suppressed | unverified | sent | send_failed | queued``.
    May raise on a store/sender bug — :func:`queue` isolates that per lead, so a raise
    here never commits a suppression row (the prospect keeps their one shot)."""
    channel = pick_channel(lead.get("contact"), prefer)
    if channel is None:
        return "needs_contact"
    msg = compose_sms(lead, campaign, footer) if channel == "sms" else compose(lead, campaign, footer)
    if content_score(msg.get("subject", "") + " " + msg["body"])["block"]:
        failures.record("outreach", "content_blocked",
                        f"{lead.get('name')}: pitch tripped the spam-content gate")
        return "blocked"
    recipient = _recipient(lead.get("contact", {}), channel)
    if do_send and channel == "email":
        # SEND-NOW: don't burn the prospect's one shot on a gated/failed send. Check
        # suppression read-only, send, and commit the never-twice row ONLY on success.
        if getattr(ledger, "is_contacted", lambda r, c: False)(recipient, campaign):
            return "suppressed"
        verdict = verify_fn(recipient)
        if not verdict.get("deliverable"):
            failures.record("outreach", "unverified_email",
                            f"{recipient}: {verdict.get('reason', 'unverifiable')} — "
                            "not sent (protects sender reputation)")
            return "unverified"         # never burn reputation on a hard bounce
        res = sender(recipient, msg["subject"], msg["body"])
        if res.get("sent"):
            ledger.log_outreach(recipient, campaign, channel)   # commit suppression
            # Record the actual email in the mail ledger so the deck MAIL panel shows it
            # (caller-side: outreach already holds the ledger). Defensive getattr keeps
            # test fakes / minimal ledgers working — same pattern as is_contacted above.
            getattr(ledger, "record_mail", lambda *a, **k: None)(
                recipient, msg["subject"], status="sent", channel="email",
                sender=res.get("from"))
            # Funnel truth: the lead row flips new→contacted ONLY on a landed send
            # (same defensive getattr — minimal test fakes keep working).
            getattr(ledger, "mark_lead_contacted", lambda r: 0)(recipient)
            return "sent"
        failures.record("outreach", "send_failed",
                        f"{recipient}: {res.get('error') or 'gated'}")
        return "send_failed"
    if do_send and channel == "sms":
        if getattr(ledger, "is_contacted", lambda r, c: False)(recipient, campaign):
            return "suppressed"
        res = sms.send(recipient, msg["body"])
        if res.get("sent"):
            ledger.log_outreach(recipient, campaign, channel)
            getattr(ledger, "mark_lead_contacted", lambda r: 0)(recipient)
            return "sent"
        failures.record("outreach", "send_failed",
                        f"{recipient}: {res.get('error') or res.get('reason') or 'gated'}")
        # NOT log_outreach — Twilio may land later; prospect keeps their one shot
        return "send_failed"
    if ledger.log_outreach(recipient, campaign, channel):
        return "queued"             # queue-only (no creds / SMS): queuing IS the action
    return "suppressed"             # already contacted for this campaign — never twice


#: Email domains owned by large corporations — a no-website SMB never has one. Skip them so
#: autonomous outreach never cold-pitches a Fortune-500 service center (e.g. savannahservice@
#: tesla.com leaked into the lead pile as "Tesla Savannah").
_CORP_EMAIL_DOMAINS: frozenset[str] = frozenset({
    "tesla.com", "walmart.com", "mcdonalds.com", "starbucks.com", "amazon.com", "target.com",
    "homedepot.com", "lowes.com", "fedex.com", "ups.com", "att.com", "verizon.com",
    "comcast.com", "cvs.com", "walgreens.com", "costco.com", "google.com", "apple.com",
    "statefarm.com", "allstate.com", "geico.com", "progressive.com", "farmers.com",
    "schlotzskys.com", "jamesavery.com", "nothingbundtcakes.com", "pita.com",
    "pitastreetfood.com", "fashionten.com", "aircraftspruce.com", "adesa.com",
    "frontierautosalesga.com",  # dealer group inbox, not local SMB owner
})
#: Role inboxes at franchises / big brands — never the owner reading cold outreach.
_CORP_LOCAL_PARTS: frozenset[str] = frozenset({
    "customerservice", "customer.service", "corporate", "support", "help", "careers",
    "hr", "recruiting", "franchise", "orders", "order", "billing", "accounts",
    "reception", "frontdesk", "administrator", "webmaster", "marketing", "media",
    "press", "legal", "compliance", "donotreply", "noreply", "no-reply", "wecare",
    "service", "info desk", "store",
})


def _is_corporate_inbox(email: str) -> bool:
    """Franchise HQs, national-brand service desks, and role inboxes — not local owners."""
    email = (email or "").lower().strip()
    if "@" not in email:
        return True
    local, _, domain = email.partition("@")
    if domain in _CORP_EMAIL_DOMAINS:
        return True
    local_norm = re.sub(r"[^a-z0-9]", "", local)
    return local_norm in {re.sub(r"[^a-z0-9]", "", p) for p in _CORP_LOCAL_PARTS}


def _is_emailable_prospect(lead: dict) -> bool:
    """A genuine no-website SMB worth cold-emailing: not a national chain, not a corporate
    inbox. Guards autonomous outreach against pitching big brands a 'you have no website' note."""
    from utah.product import leads as leads_mod

    if leads_mod.is_national_chain(lead.get("name") or ""):
        return False
    email = ((lead.get("contact") or {}).get("email") or "").lower()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    if not domain or _is_corporate_inbox(email):
        return False
    return True


def _is_phone_prospect(lead: dict) -> bool:
    """Genuine local SMB worth cold-texting — not a national chain or toll-free line."""
    from utah.product import leads as leads_mod

    if leads_mod.is_national_chain(lead.get("name") or ""):
        return False
    phone = ((lead.get("contact") or {}).get("phone") or "").strip()
    if not phone:
        return False
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "23456789":
        return False
    # Toll-free / premium — never a local owner's cell (88885091616 leaked from Maps).
    if digits[0:3] in {"800", "888", "877", "866", "855", "844", "833", "822", "900"}:
        return False
    return True


def run_scheduled(campaign: str = DEFAULT_CAMPAIGN, limit: int = DAILY_OUTREACH, *,
                  ledger=None, foundation_gate=None, send_fn=None,
                  channel: str | None = None, now_hour: int | None = None,
                  verify_fn=None) -> dict:
    """``com.utah.outreach`` cron — SMB small-business outreach only (OSM + Maps).
    Probate heirs are in the ``probate`` table and never enter this path.

    Cadence is enforced HERE, not just in the plist: a send only happens inside the
    local business window (``config.within_business_hours``) and is capped at *limit*
    per run (== per hour, the cron fires hourly). A run kicked at 5am — by a manual
    kickstart or a misconfigured cron — refuses with a documented skip and sends
    NOTHING. ``channel='auto'`` (default) emails leads that have an email and TEXTS
    (SMS→iMessage) phone-only leads, filling one combined *limit* — so a phone-only
    lead is still reached (Michael's directive)."""
    from utah import foundation

    if campaign != SMB_OUTREACH_CAMPAIGN:
        return {"campaign": campaign, "sent": 0, "queued": 0,
                "reason": "com.utah.outreach is SMB-only; probate uses separate pipeline"}
    # HARD business-hours gate (rule in the machine): never text/email a prospect at 5am.
    if not config.within_business_hours(now_hour):
        h = now_hour if now_hour is not None else "now"
        return {"campaign": campaign, "sent": 0, "queued": 0, "skipped": True,
                "reason": (f"outside business hours ({config.OUTREACH_HOUR_START:02d}:00–"
                           f"{config.OUTREACH_HOUR_END:02d}:00 local) — hour={h}, send refused")}
    gate = foundation.gate_cron if foundation_gate is None else foundation_gate
    skip = gate("outreach")
    if skip:
        return skip
    if ledger is None:
        from utah.product.ledger import Ledger
        ledger = Ledger()
    ch = (channel or OUTREACH_CHANNEL).lower()

    if ch == "sms":
        candidates = ledger.uncontacted_phone_leads(campaign, max(limit * 4, limit))
        leads = [l for l in candidates if _is_phone_prospect(l)][:limit]
        if not leads:
            return {"campaign": campaign, "channel": "sms", "sent": 0, "queued": 0,
                    "reason": "no phone SMB prospects (chains filtered)"}
        result = queue(ledger, campaign, leads, footer=default_footer(),
                       can_send=True, send_fn=send_fn, prefer="sms", verify_fn=verify_fn)
        result["channel"] = "sms"
    elif ch == "email":
        candidates = ledger.uncontacted_email_leads(campaign, max(limit * 4, limit))
        leads = [l for l in candidates if _is_emailable_prospect(l)][:limit]
        if not leads:
            # No early return: follow-ups below must still run — an exhausted cold
            # pool is exactly when the due day-3/day-7 nudges are the day's sends.
            result = {"campaign": campaign, "channel": "email", "sent": 0, "queued": 0,
                      "reason": "no emailable SMB prospects (chains/corporate filtered)"}
        else:
            result = queue(ledger, campaign, leads, footer=default_footer(),
                           can_send=True, send_fn=send_fn, prefer="email", verify_fn=verify_fn)
            result["channel"] = "email"
    else:  # auto — email-first, then fill the remaining quota with text (SMS/iMessage)
        result = _run_auto(ledger, campaign, limit, send_fn, verify_fn=verify_fn)

    # Follow-ups ride every email-capable run: due day-3/day-7 nudges go out FIRST in
    # spirit (warmer than cold) but are accounted separately so the cold quota and the
    # plist contract stay untouched. The per-inbox daily mail cap bounds the total.
    if ch in ("email", "auto"):
        try:
            fu = run_followups(ledger=ledger, send_fn=send_fn, now_hour=now_hour)
            if fu.get("sent"):
                result["followups"] = fu
                result["sent"] = result.get("sent", 0) + fu["sent"]
        except Exception as exc:  # noqa: BLE001 — follow-ups must never break cold sends
            log.warning("outreach follow-ups failed: %s", exc)

    log.info("outreach run_scheduled: campaign=%s channel=%s sent=%d queued=%d",
             campaign, result.get("channel", ch), result.get("sent", 0), result.get("queued", 0))
    from utah import alerts
    alerts.leads_probate(result, kind="outreach")   # daily pipeline push (never raises)
    return result


def _run_auto(ledger, campaign: str, limit: int, send_fn, *, verify_fn=None) -> dict:
    """Email-first, text-fallback: send emailable leads via email, then fill the rest
    of *limit* with phone-only leads via text (SMS→iMessage). When every inbox has hit
    its daily cap, the full quota shifts to SMS so the day isn't dead after 8am."""
    footer = default_footer()
    mail_exhausted = mail.inboxes_exhausted()
    email_budget = 0 if mail_exhausted else min(limit, mail.sends_remaining())

    email_cand = ledger.uncontacted_email_leads(campaign, max(limit * 4, limit))
    email_leads = [l for l in email_cand if _is_emailable_prospect(l)][:email_budget]
    seen_ids = {l.get("id") for l in email_leads if l.get("id") is not None}

    remaining = limit - len(email_leads)
    phone_leads: list[dict] = []
    if remaining > 0:
        phone_cand = ledger.uncontacted_phone_leads(campaign, max(remaining * 4, remaining))
        for l in phone_cand:
            if l.get("id") is not None and l.get("id") in seen_ids:
                continue
            if _is_phone_prospect(l):
                phone_leads.append(l)
            if len(phone_leads) >= remaining:
                break

    if not email_leads and not phone_leads:
        reason = ("no textable SMB prospects (mail capped, SMS pool empty)"
                  if mail_exhausted else
                  "no emailable or textable SMB prospects (chains/corporate filtered)")
        return {"campaign": campaign, "channel": "auto", "sent": 0, "queued": 0,
                "reason": reason, "mail_exhausted": mail_exhausted}

    er = queue(ledger, campaign, email_leads, footer=footer, can_send=True,
               send_fn=send_fn, prefer="email", verify_fn=verify_fn) if email_leads else {}
    sr = queue(ledger, campaign, phone_leads, footer=footer, can_send=True,
               send_fn=send_fn, prefer="sms", verify_fn=verify_fn) if phone_leads else {}
    return {
        "campaign": campaign, "channel": "auto",
        "sent": er.get("sent", 0) + sr.get("sent", 0),
        "queued": er.get("queued", 0) + sr.get("queued", 0),
        "suppressed": er.get("suppressed", 0) + sr.get("suppressed", 0),
        "blocked": er.get("blocked", 0) + sr.get("blocked", 0),
        "needs_contact": er.get("needs_contact", 0) + sr.get("needs_contact", 0),
        "unverified": er.get("unverified", 0) + sr.get("unverified", 0),
        "mail_exhausted": mail_exhausted,
        "email": {"pulled": len(email_leads), "sent": er.get("sent", 0)},
        "text": {"pulled": len(phone_leads), "sent": sr.get("sent", 0)},
    }


__all__ = ["compose", "compose_sms", "content_score", "pick_channel", "queue", "default_footer",
           "run_scheduled", "DAILY_OUTREACH", "DEFAULT_CAMPAIGN", "OUTREACH_CHANNEL",
           "DEFAULT_FOOTER", "SPAM_BLOCK_THRESHOLD", "SMB_OUTREACH_CAMPAIGN",
           "PROBATE_OUTREACH_CAMPAIGN"]
