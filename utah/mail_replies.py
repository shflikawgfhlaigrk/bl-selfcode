"""Reply detection — the funnel's conversion eye (was the #1 missing revenue piece).

Until 2026-06-09 the outreach machine was WRITE-ONLY: 30 humans pitched, zero ability
to see an answer. ``com.utah.mailcheck`` is just an SMTP login probe; nothing read the
inbox, so a "yes, build my site" would sit unseen for hours/days and a hard bounce was
recorded as ``sent`` forever. This module closes the loop:

    poll() → IMAP the sending inbox(es) → classify each new message →
        • reply from a PITCHED prospect → mail_replies row + 💰 phone page (critical)
        • bounce (mailer-daemon DSN)    → mail_replies row + mail_ledger → 'bounced'
        • everything else (vendor noise) → ignored

State (last-seen IMAP UID per account) persists across the 15-min cron in
``~/.utah/run/mail_replies_state.json`` so each message is examined once; the
``mail_replies.message_id`` UNIQUE key makes recording idempotent even if state is
lost. Honest-gated like every adapter: no creds → documented gate, never fakes.
The IMAP fetch is injectable for tests.
"""
from __future__ import annotations

import email
import email.header
import imaplib
import json
import logging
import re

from utah import alerts, config, failures, mail
from utah.daemon import runtime
from utah.product.ledger import Ledger, LedgerError

log = logging.getLogger("utah.mail_replies")

IMAP_HOST_DEFAULT = "imap.gmail.com"
STATE = runtime.UTAH_HOME / "run" / "mail_replies_state.json"

_BOUNCE_SENDERS = ("mailer-daemon", "postmaster")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


# --- pure helpers (unit-tested directly) ----------------------------------------


def classify(sender: str, pitched: set[str]) -> str:
    """``bounce`` | ``reply`` | ``other`` for an inbound message's From address."""
    s = (sender or "").strip().lower()
    if any(b in s for b in _BOUNCE_SENDERS):
        return "bounce"
    if s in pitched:
        return "reply"
    return "other"


def bounced_recipient(snippet: str, pitched: set[str]) -> str | None:
    """The pitched address a DSN is about — first pitched email found in the body."""
    for cand in _EMAIL_RE.findall(snippet or ""):
        if cand.strip().lower() in pitched:
            return cand.strip().lower()
    return None


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        parts = email.header.decode_header(value)
        return "".join(
            p.decode(enc or "utf-8", "replace") if isinstance(p, bytes) else p
            for p, enc in parts
        ).strip()
    except Exception:  # noqa: BLE001 — a mangled header must not kill the poll
        return str(value).strip()


def _sender_email(from_header: str) -> str:
    m = _EMAIL_RE.search(from_header or "")
    return m.group(0).lower() if m else (from_header or "").strip().lower()


# --- state ----------------------------------------------------------------------


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))


# --- the real IMAP fetch (injectable) --------------------------------------------


def _imap_fetch(account: dict, last_uid: int) -> list[dict]:  # pragma: no cover — network
    """New messages (uid > last_uid) for *account*: sender/subject/message_id/snippet."""
    host = account.get("imap_host", IMAP_HOST_DEFAULT)
    conn = imaplib.IMAP4_SSL(host, timeout=30)
    try:
        conn.login(account["from"], account["app_password"])
        conn.select("INBOX", readonly=True)
        if last_uid > 0:
            criteria = f"UID {last_uid + 1}:*"
        else:
            # First poll for this inbox: bound to the last 2 days — UID 1:* would
            # walk the entire multi-year mailbox on a years-old Gmail.
            import datetime as _dt
            since = (_dt.date.today() - _dt.timedelta(days=2)).strftime("%d-%b-%Y")
            criteria = f"SINCE {since}"
        ok, data = conn.uid("search", None, criteria)
        if ok != "OK":
            return []
        out: list[dict] = []
        for uid_b in (data[0] or b"").split():
            uid = int(uid_b)
            if uid <= last_uid:  # gmail returns the last UID even when none are new
                continue
            ok, msg_data = conn.uid("fetch", uid_b, "(BODY.PEEK[])")
            if ok != "OK" or not msg_data or msg_data[0] is None:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            snippet = ""
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True) or b""
                    snippet = payload.decode(part.get_content_charset() or "utf-8",
                                             "replace")[:1000]
                    break
            out.append({
                "uid": uid,
                "sender": _sender_email(_decode(msg.get("From"))),
                "subject": _decode(msg.get("Subject")),
                "message_id": _decode(msg.get("Message-ID")) or f"uid:{account['from']}:{uid}",
                "snippet": snippet,
            })
        return out
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass


# --- the poll --------------------------------------------------------------------


def poll(*, fetch_fn=None, ledger: Ledger | None = None, alert_fn=None) -> dict:
    """One poll pass over every sending inbox. Returns counts; never raises."""
    pool = mail.accounts()
    if not pool:
        return {"ok": False, "gated": True, "reason": "no mail creds"}
    ledger = ledger or Ledger()
    fetch = fetch_fn or _imap_fetch
    alert = alert_fn or alerts.prospect_reply
    try:
        pitched = ledger.pitched_recipients()
    except LedgerError as exc:
        failures.record("mail_replies", "ledger_unreachable", str(exc)[:200])
        return {"ok": False, "gated": False, "error": str(exc)}
    # The owner's own mail (briefs, spotlights, self-test proofs) lands in this same
    # inbox FROM addresses that the ledgers also contain as recipients. The owner is
    # never a prospect — without this, the first live poll paged 18 false "replies".
    own = {a["from"].strip().lower() for a in pool} | {config.OWNER_EMAIL.strip().lower()}
    pitched -= own

    state = _load_state()
    checked = replies = bounces = 0
    for account in pool:
        addr = account["from"]
        last_uid = int(state.get(addr, 0))
        try:
            msgs = fetch(account, last_uid)
        except Exception as exc:  # noqa: BLE001 — one dead inbox must not kill the rest
            failures.record("mail_replies", "imap_failed", f"{addr}: {exc}"[:200])
            continue
        for m in msgs:
            checked += 1
            last_uid = max(last_uid, int(m.get("uid", last_uid)))
            kind = classify(m.get("sender", ""), pitched)
            if kind == "other":
                continue
            new = ledger.record_reply(
                m.get("sender", ""), m.get("subject", ""), kind,
                m.get("message_id", ""), (m.get("snippet") or "")[:1000],
            )
            if not new:
                continue
            if kind == "reply":
                replies += 1
                alert(m.get("sender", ""), m.get("subject", ""))
            else:
                bounces += 1
                target = bounced_recipient(m.get("snippet", ""), pitched)
                if target:
                    ledger.mark_bounced(target)
        state[addr] = last_uid
    _save_state(state)
    return {"ok": True, "gated": False, "checked": checked,
            "replies": replies, "bounces": bounces}


def run_scheduled() -> dict:
    """Cron entrypoint (``com.utah.replies``, every 15 min)."""
    res = poll()
    log.info("mail_replies: %s", res)
    return res


__all__ = ["poll", "run_scheduled", "classify", "bounced_recipient", "STATE"]
