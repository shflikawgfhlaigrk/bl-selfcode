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

import datetime
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


def imap_host_for(account: dict) -> str:
    """The IMAP host for *account*: an explicit ``imap_host`` wins; otherwise it's derived
    from the SMTP host by swapping the leading ``smtp`` for ``imap`` (smtp.privateemail.com
    → imap.privateemail.com, smtp.gmail.com → imap.gmail.com).

    Regression this kills: the Private Email inboxes (info@/delivery@blacklabelbots.com) set
    only ``smtp_host``, so the reader fell back to the gmail default and threw Namecheap creds
    at ``imap.gmail.com`` — AUTHENTICATIONFAILED every 15 min, with real replies stranded
    unread. Deriving the host from SMTP means a sending account is always readable from the
    SAME provider, and the gmail default still applies when nothing is configured at all."""
    if account.get("imap_host"):
        return account["imap_host"]
    smtp = (account.get("smtp_host") or "").strip().lower()
    if smtp.startswith("smtp."):
        return "imap." + smtp[len("smtp."):]
    return IMAP_HOST_DEFAULT

_BOUNCE_SENDERS = frozenset({"mailer-daemon", "postmaster"})  # exact DSN local parts
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


# --- pure helpers (unit-tested directly) ----------------------------------------


def classify(sender: str, pitched: set[str]) -> str:
    """``bounce`` | ``reply`` | ``other`` for an inbound message's From address.

    Pitched membership wins: a prospect we pitched AT ``postmaster@their-domain``
    answering is a REPLY — misfiling it as a bounce would silently eat a "yes".
    Bounce detection then matches the DSN local part EXACTLY (real DSNs arrive
    from ``mailer-daemon@``/``postmaster@``); the old substring test misfiled
    any human whose address merely contained those words.
    """
    s = (sender or "").strip().lower()
    if s in pitched:
        return "reply"
    if s.split("@", 1)[0] in _BOUNCE_SENDERS:
        return "bounce"
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
    """Last-seen UID per inbox. A missing/corrupt file degrades to {} — every
    message is then re-examined and ``message_id`` idempotency absorbs the rerun."""
    try:
        data = json.loads(STATE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _last_uid(state: dict, addr: str) -> int:
    """The persisted cursor for *addr*; a corrupt value degrades to 0 (full rescan)."""
    try:
        return max(0, int(state.get(addr, 0)))
    except (TypeError, ValueError):
        return 0


def _save_state(state: dict) -> None:
    """Persist the cursors. A write failure is documented, never fatal — the next
    poll re-examines the same messages and idempotency absorbs the duplicates."""
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state))
    except OSError as exc:
        failures.record("mail_replies", "state_write_failed", str(exc)[:200])


# --- the real IMAP fetch (testable via an injected connection) --------------------


def _search_criteria(last_uid: int) -> str:
    """IMAP UID-search criteria for *last_uid*. A first poll (cursor 0) is bounded
    to the last 2 days — ``UID 1:*`` would walk an entire multi-year Gmail."""
    if last_uid > 0:
        return f"UID {last_uid + 1}:*"
    since = (datetime.date.today() - datetime.timedelta(days=2)).strftime("%d-%b-%Y")
    return f"SINCE {since}"


def _message_summary(raw: bytes, account_from: str, uid: int) -> dict:
    """One fetched message → the dict the poll consumes. The ``message_id`` falls
    back to an account-scoped UID key so the idempotency key is never empty."""
    msg = email.message_from_bytes(raw)
    snippet = ""
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True) or b""
            snippet = payload.decode(part.get_content_charset() or "utf-8",
                                     "replace")[:1000]
            break
    return {
        "uid": uid,
        "sender": _sender_email(_decode(msg.get("From"))),
        "subject": _decode(msg.get("Subject")),
        "message_id": _decode(msg.get("Message-ID")) or f"uid:{account_from}:{uid}",
        "snippet": snippet,
    }


def _default_conn(host: str):  # pragma: no cover — opens a real TLS socket
    return imaplib.IMAP4_SSL(host, timeout=30)


def _imap_fetch(account: dict, last_uid: int, *, conn_factory=None) -> list[dict]:
    """New messages (uid > last_uid) for *account*: sender/subject/message_id/snippet.

    *conn_factory* (keyword-only, so ``poll``'s positional ``fetch(account, last)``
    contract is untouched) builds the IMAP connection — injected in tests so every
    branch here is provable off the network; the default opens a 30s-bounded TLS
    socket. Exceptions propagate: ``poll`` documents them per-inbox as
    ``imap_failed`` without losing the other accounts.
    """
    host = imap_host_for(account)
    conn = (conn_factory or _default_conn)(host)
    try:
        conn.login(account["from"], account["app_password"])
        conn.select("INBOX", readonly=True)
        ok, data = conn.uid("search", None, _search_criteria(last_uid))
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
            out.append(_message_summary(msg_data[0][1], account["from"], uid))
        return out
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001 — releasing a dead connection is best-effort
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
    counts = {"checked": 0, "replies": 0, "bounces": 0}
    for account in pool:
        addr = account["from"]
        last_uid = _last_uid(state, addr)
        try:
            msgs = fetch(account, last_uid)
        except Exception as exc:  # noqa: BLE001 — one dead inbox must not kill the rest
            failures.record("mail_replies", "imap_failed", f"{addr}: {exc}"[:200])
            continue
        state[addr] = _process_inbox(msgs, last_uid, pitched, ledger, alert, counts)
    _save_state(state)
    return {"ok": True, "gated": False, **counts}


def _process_inbox(msgs, last_uid: int, pitched: set[str], ledger, alert,
                   counts: dict) -> int:
    """Handle one inbox's new messages; returns the advanced UID cursor.

    The cursor only moves past a message once it is FULLY handled. A ledger write
    failure freezes the cursor BEFORE the failed message and stops this inbox's
    pass — advancing past an unrecorded reply would silently lose a prospect
    saying "yes"; the healed ledger re-receives it next poll. Paging and the
    bounce flip are best-effort: their failures are documented, never fatal,
    and never block the cursor (``message_id`` idempotency covers reruns).
    """
    cursor = last_uid
    for m in msgs:
        try:
            uid = int(m.get("uid", cursor))
        except (AttributeError, TypeError, ValueError):
            # Garbage from the fetch (non-dict, non-int uid): documented, skipped —
            # there is no cursor position to advance to for an unidentifiable message.
            failures.record("mail_replies", "process_failed",
                            f"malformed message {m!r}"[:200])
            continue
        counts["checked"] += 1
        kind = classify(m.get("sender", ""), pitched)
        if kind == "other":
            cursor = max(cursor, uid)
            continue
        try:
            new = ledger.record_reply(
                m.get("sender", ""), m.get("subject", ""), kind,
                m.get("message_id", ""), (m.get("snippet") or "")[:1000],
            )
        except LedgerError as exc:
            failures.record("mail_replies", "ledger_write_failed",
                            f"uid {uid}: {exc}"[:200])
            break  # ledger is down: stop here so this and later messages are retried
        cursor = max(cursor, uid)
        if not new:  # message_id already recorded (state was lost) — never re-page
            continue
        if kind == "reply":
            counts["replies"] += 1
            try:
                alert(m.get("sender", ""), m.get("subject", ""))
            except Exception as exc:  # noqa: BLE001 — a dead pager must not lose the poll
                failures.record("mail_replies", "alert_failed", str(exc)[:200])
        else:
            counts["bounces"] += 1
            target = bounced_recipient(m.get("snippet", ""), pitched)
            if target:
                try:
                    ledger.mark_bounced(target)
                except LedgerError as exc:
                    failures.record("mail_replies", "bounce_mark_failed",
                                    f"{target}: {exc}"[:200])
    return cursor


def run_scheduled() -> dict:
    """Cron entrypoint (``com.utah.replies``, every 15 min)."""
    res = poll()
    log.info("mail_replies: %s", res)
    return res


__all__ = ["poll", "run_scheduled", "classify", "bounced_recipient", "STATE"]
