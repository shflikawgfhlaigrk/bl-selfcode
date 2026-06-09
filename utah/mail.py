"""Mail capability — real SMTP send, with multi-account ROTATION for cold volume.

Ace's mail/email_compose transitions HERE as a capability behind the brain, not an agent.
The send path is real SMTP/SSL via Gmail app-passwords. Cold volume must spread across
several inboxes so no single account spikes (deliverability), so sending rotates round-robin
across a POOL of accounts:

    ~/.utah/secrets/email_accounts.json   # [{"from": "...", "app_password": "...", "smtp_host": "..."}, ...]

With that pool absent we fall back to the single ``gmail.json`` (back-compat). A persistent
on-disk cursor (``~/.utah/run/mail_rotation.json``) keeps the rotation even ACROSS the
separate hourly cron processes. Utah never fakes a send: with no creds it documents the gate
and returns ``sent=False``. The sender is injectable for tests.

Single-account creds shape:
    {"from": "you@gmail.com", "app_password": "abcd efgh ijkl mnop", "smtp_host": "smtp.gmail.com"}
"""
from __future__ import annotations

import json
import logging
import os

from utah import config, failures
from utah.daemon import runtime

log = logging.getLogger("utah.mail")

GMAIL_CREDS = runtime.UTAH_HOME / "secrets" / "gmail.json"
#: Optional pool of sending accounts (a JSON list); when present it rotates across all of them.
ACCOUNTS_FILE = runtime.UTAH_HOME / "secrets" / "email_accounts.json"
#: Persistent round-robin cursor + per-day per-account counts, shared across cron processes.
ROTATION_STATE = runtime.UTAH_HOME / "run" / "mail_rotation.json"
#: Safe COLD daily cap PER inbox. Past ~20-30/day cold, a Gmail's spam-complaint rate blows
#: past Google's 0.3% threshold and the inbox/domain reputation dies (then suspension). The
#: rotation refuses to exceed this per account so volume can never burn a sender. 0 = no cap.
PER_ACCOUNT_DAILY = int(os.environ.get("UTAH_MAIL_PER_ACCOUNT_DAILY", "30"))


def _norm(account: dict) -> dict:
    return {
        "from": config.normalize_owner_email(account.get("from")),
        "app_password": account.get("app_password"),
        "smtp_host": account.get("smtp_host", "smtp.gmail.com"),
    }


def accounts() -> list[dict]:
    """The pool of sending accounts. ``email_accounts.json`` (a non-empty list) wins; else
    the single ``gmail.json``; else empty (gated). Only well-formed accounts are returned."""
    try:
        data = json.loads(ACCOUNTS_FILE.read_text())
        if isinstance(data, list):
            pool = [_norm(a) for a in data
                    if isinstance(a, dict) and a.get("from") and a.get("app_password")]
            if pool:
                return pool
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        pass
    try:
        a = json.loads(GMAIL_CREDS.read_text())
        if a.get("from") and a.get("app_password"):
            return [_norm(a)]
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        pass
    return []


def creds_available() -> bool:
    return bool(accounts())


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


def _load_state() -> dict:
    try:
        s = json.loads(ROTATION_STATE.read_text())
        if isinstance(s, dict):
            return s
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        pass
    return {}


def _save_state(state: dict) -> None:
    try:
        ROTATION_STATE.parent.mkdir(parents=True, exist_ok=True)
        ROTATION_STATE.write_text(json.dumps(state))
    except OSError:
        pass


def _next_account() -> dict | None:
    """Round-robin the next UNDER-CAP account, advancing a disk-persisted cursor and per-day
    per-account counts (shared across the hourly cron processes). Returns None when EVERY
    inbox has hit :data:`PER_ACCOUNT_DAILY` — the send path then gates rather than burning an
    inbox. Counts reset on a new local date."""
    pool = accounts()
    if not pool:
        return None
    today = _today()
    state = _load_state()
    if state.get("date") != today:
        state = {"cursor": int(state.get("cursor", 0)), "date": today, "counts": {}}
    cursor = int(state.get("cursor", 0))
    counts = dict(state.get("counts", {}))
    cap = PER_ACCOUNT_DAILY
    chosen = None
    for i in range(len(pool)):
        idx = (cursor + i) % len(pool)
        acct = pool[idx]
        if cap <= 0 or counts.get(acct["from"], 0) < cap:
            chosen = acct
            cursor = idx + 1
            counts[acct["from"]] = counts.get(acct["from"], 0) + 1
            break
    _save_state({"cursor": cursor, "date": today, "counts": counts})
    return chosen


def _send_via(account: dict, to: str, subject: str, body: str) -> None:
    """Real adapter — SMTP/SSL send through one specific account's app-password."""
    import smtplib
    import ssl
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = account["from"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL(account.get("smtp_host", "smtp.gmail.com"), 465,
                          context=ssl.create_default_context()) as s:
        s.login(account["from"], account["app_password"])
        s.send_message(msg)


def _gmail_login_probe() -> None:
    """Auth-only probe: open SMTP/SSL, log in, hang up — proves creds without sending."""
    import json as _json
    import smtplib
    import ssl

    creds = _json.loads(GMAIL_CREDS.read_text())
    creds["from"] = config.normalize_owner_email(creds.get("from"))
    with smtplib.SMTP_SSL(creds.get("smtp_host", "smtp.gmail.com"), 465,
                          context=ssl.create_default_context(), timeout=15) as s:
        s.login(creds["from"], creds["app_password"])


def verify(*, probe_fn=None) -> dict:
    """Probe SMTP auth WITHOUT sending (login then disconnect). Returns ``{ok, gated?, error?}``;
    never raises. ``com.utah.mailcheck`` calls this and pages the phone the instant auth breaks.
    (Single-account probe today; a pool-wide verify_all is a clean follow-up.)"""
    if probe_fn is None and not creds_available():
        return {"ok": False, "gated": True}
    prober = probe_fn or _gmail_login_probe
    try:
        prober()
        return {"ok": True, "gated": False}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "gated": False, "error": str(exc)}


def send(to: str, subject: str, body: str, *, send_fn=None) -> dict:
    """Send an email. With an injected ``send_fn`` it uses that; else it rotates across the
    account pool and sends via real SMTP; with no creds it documents the gate and returns
    ``sent=False`` (never fabricates). Never raises. Result carries the ``from`` account used."""
    if send_fn is None and not creds_available():
        failures.record("mail", "gated",
                         f"email to {to[:40]} gated: no sending creds "
                         f"({ACCOUNTS_FILE} or {GMAIL_CREDS}) — Michael's business input")
        return {"sent": False, "gated": True, "to": to}
    if send_fn is not None:
        try:
            send_fn(to, subject, body)
            log.info("mail: sent to %s (%s)", to, subject[:40])
            return {"sent": True, "gated": False, "to": to}
        except Exception as exc:  # noqa: BLE001
            failures.record("mail", "send_failed", f"{to[:40]}: {exc}")
            return {"sent": False, "gated": False, "error": str(exc), "to": to}
    account = _next_account()
    if account is None:
        failures.record("mail", "capped",
                        f"email to {to[:40]} held: all {len(accounts())} inbox(es) hit the "
                        f"safe daily cap (UTAH_MAIL_PER_ACCOUNT_DAILY={PER_ACCOUNT_DAILY})")
        return {"sent": False, "gated": True, "to": to, "reason": "all_inboxes_capped"}
    try:
        _send_via(account, to, subject, body)
        log.info("mail: sent to %s from %s (%s)", to, account["from"], subject[:40])
        return {"sent": True, "gated": False, "to": to, "from": account["from"]}
    except Exception as exc:  # noqa: BLE001
        failures.record("mail", "send_failed", f"{to[:40]} via {account.get('from')}: {exc}")
        return {"sent": False, "gated": False, "error": str(exc), "to": to,
                "from": account.get("from")}


__all__ = ["send", "verify", "creds_available", "accounts",
           "GMAIL_CREDS", "ACCOUNTS_FILE", "ROTATION_STATE"]
