"""Mail capability — ready-skeleton, GATED on Michael's Gmail/sending creds.

Ace's mail/email_compose transitions HERE as a capability behind the brain, not an agent.
The full send path is wired (SMTP over SSL with a Gmail app-password read from
``~/.utah/secrets/gmail.json``). It activates the instant that creds file lands; until then
``send`` documents the gate and returns ``sent=False`` — it NEVER fakes a send. This is what
unblocks outreach send + the morning-brief email. The sender is injectable for tests.

Creds file shape (when Michael provides it):
    {"from": "you@gmail.com", "app_password": "abcd efgh ijkl mnop", "smtp_host": "smtp.gmail.com"}
"""
from __future__ import annotations

import logging

from utah import config, failures
from utah.daemon import runtime

log = logging.getLogger("utah.mail")

GMAIL_CREDS = runtime.UTAH_HOME / "secrets" / "gmail.json"


def creds_available() -> bool:
    return GMAIL_CREDS.exists()


def _gmail_send(to: str, subject: str, body: str) -> None:
    """Real adapter — activates when GMAIL_CREDS lands. SMTP/SSL via app-password."""
    import json
    import smtplib
    import ssl
    from email.message import EmailMessage

    creds = json.loads(GMAIL_CREDS.read_text())
    creds["from"] = config.normalize_owner_email(creds.get("from"))
    msg = EmailMessage()
    msg["From"] = creds["from"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL(creds.get("smtp_host", "smtp.gmail.com"), 465,
                          context=ssl.create_default_context()) as s:
        s.login(creds["from"], creds["app_password"])
        s.send_message(msg)


def _gmail_login_probe() -> None:
    """Auth-only probe: open SMTP/SSL, log in, hang up — proves the creds without sending."""
    import json
    import smtplib
    import ssl

    creds = json.loads(GMAIL_CREDS.read_text())
    creds["from"] = config.normalize_owner_email(creds.get("from"))
    with smtplib.SMTP_SSL(creds.get("smtp_host", "smtp.gmail.com"), 465,
                          context=ssl.create_default_context(), timeout=15) as s:
        s.login(creds["from"], creds["app_password"])


def verify(*, probe_fn=None) -> dict:
    """Probe SMTP auth WITHOUT sending (login then disconnect). Returns
    ``{ok, gated?, error?}``; never raises. The ``com.utah.mailcheck`` cron calls this and
    pages the phone the instant auth breaks — so a dead app-password surfaces immediately
    instead of being discovered hours later on the deck."""
    if probe_fn is None and not creds_available():
        return {"ok": False, "gated": True}
    prober = probe_fn or _gmail_login_probe
    try:
        prober()
        return {"ok": True, "gated": False}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "gated": False, "error": str(exc)}


def send(to: str, subject: str, body: str, *, send_fn=None) -> dict:
    """Send an email. With an injected ``send_fn`` or real creds present, it sends; with
    neither it documents the gate and returns ``sent=False`` (never fabricates). Never raises."""
    if send_fn is None and not creds_available():
        failures.record("mail", "gated",
                        f"email to {to[:40]} gated: no Gmail creds at {GMAIL_CREDS} "
                        "(Michael's business input)")
        return {"sent": False, "gated": True, "to": to}
    sender = send_fn or _gmail_send
    try:
        sender(to, subject, body)
        log.info("mail: sent to %s (%s)", to, subject[:40])
        return {"sent": True, "gated": False, "to": to}
    except Exception as exc:  # noqa: BLE001
        failures.record("mail", "send_failed", f"{to[:40]}: {exc}")
        return {"sent": False, "gated": False, "error": str(exc), "to": to}


__all__ = ["send", "verify", "creds_available", "GMAIL_CREDS"]
