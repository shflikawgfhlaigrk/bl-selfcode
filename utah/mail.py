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
    {"from": "info@blacklabelbots.com", "app_password": "...", "smtp_host": "smtp.privateemail.com"}

Set ``from`` to :data:`utah.config.BLB_FROM_EMAIL` (default ``info@blacklabelbots.com``).
If using Google Workspace, authorize that address as a Gmail **Send mail as** alias for
the account that holds the app password (or use a dedicated Workspace user for info@).
"""
from __future__ import annotations

import contextlib
import datetime
import fcntl
import json
import logging
import os
from pathlib import Path

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
#: Hard bound on every SMTP connection (connect + each socket op). An unbounded
#: SMTP_SSL hang inside the hourly outreach cron would silently stall the whole
#: send window; 30s is generous for Gmail and still fails fast enough to retry.
SMTP_TIMEOUT = float(os.environ.get("UTAH_MAIL_SMTP_TIMEOUT", "30"))
#: ``sends_remaining`` with the per-account cap DISABLED: callers do
#: ``min(limit, sends_remaining())`` so the value must be huge enough to never
#: throttle, yet a real bounded int (arithmetic stays sane, never ``inf``).
_UNCAPPED_SENDS = 9999


def _header_unsafe(value: str) -> bool:
    """True when *value* can't go into an SMTP header: CR/LF would smuggle extra
    headers through one message (CWE-93 — a recipient of ``a@b\\r\\nBcc: ...`` is a
    spam blast wearing one send's clothes), NUL corrupts the wire."""
    return any(c in value for c in ("\r", "\n", "\x00"))


def _norm(account: dict) -> dict:
    return {
        "from": config.normalize_blb_from_email(account.get("from")),
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
    return datetime.date.today().isoformat()


@contextlib.contextmanager
def _state_lock():
    """Serialize the rotation state's read-modify-write across THREADS and
    PROCESSES (the hourly crons are separate processes sharing one state file).
    Without this, two concurrent senders read the same counts, both pick an
    account, and one increment is lost — a lost count is an extra cold send
    past the per-inbox reputation cap. flock on a sidecar lockfile; on any OS
    failure the lock degrades to a no-op (best-effort: a missed lock falls
    back to the old racy behavior, it never blocks a send)."""
    lock_path = Path(str(ROTATION_STATE) + ".lock")
    fh = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "a")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except OSError as exc:
        log.debug("mail: rotation lock unavailable (%s) — proceeding unlocked", exc)
        if fh is not None:
            with contextlib.suppress(OSError):
                fh.close()
            fh = None
    try:
        yield
    finally:
        if fh is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                fh.close()


def _load_state() -> dict:
    try:
        s = json.loads(ROTATION_STATE.read_text())
        if isinstance(s, dict):
            return s
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        pass
    return {}


def _save_state(state: dict) -> None:
    """Persist the rotation cursor/counts. A failed save is LOGGED, never raised —
    a silently lost count would let tomorrow's crons oversend past the per-inbox
    cap with no trace of why."""
    try:
        ROTATION_STATE.parent.mkdir(parents=True, exist_ok=True)
        ROTATION_STATE.write_text(json.dumps(state))
    except OSError as exc:
        log.warning("mail: could not persist rotation state to %s (%s) — "
                    "per-day counts may undercount", ROTATION_STATE, exc)


def _next_account() -> dict | None:
    """Round-robin the next UNDER-CAP account, advancing a disk-persisted cursor and per-day
    per-account counts (shared across the hourly cron processes). Returns None when EVERY
    inbox has hit :data:`PER_ACCOUNT_DAILY` — the send path then gates rather than burning an
    inbox. Counts reset on a new local date."""
    pool = accounts()
    if not pool:
        return None
    with _state_lock():  # the read-modify-write below must be atomic across crons
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


def inboxes_exhausted() -> bool:
    """True when every sending account has hit :data:`PER_ACCOUNT_DAILY` for today."""
    pool = accounts()
    if not pool:
        return True
    if PER_ACCOUNT_DAILY <= 0:
        return False
    state = _load_state()
    counts = dict(state.get("counts", {})) if state.get("date") == _today() else {}
    return all(counts.get(acct["from"], 0) >= PER_ACCOUNT_DAILY for acct in pool)


def sends_remaining() -> int:
    """How many cold emails can still go out today across the pool (read-only)."""
    pool = accounts()
    if not pool:
        return 0
    if PER_ACCOUNT_DAILY <= 0:  # cap disabled — effectively unlimited, but a real int
        return len(pool) * _UNCAPPED_SENDS
    state = _load_state()
    counts = dict(state.get("counts", {})) if state.get("date") == _today() else {}
    return sum(max(0, PER_ACCOUNT_DAILY - counts.get(acct["from"], 0)) for acct in pool)


def _send_via(account: dict, to: str, subject: str, body: str) -> None:
    """Real adapter — SMTP/SSL send through one specific account's app-password."""
    import smtplib
    import ssl
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = account["from"]
    msg["To"] = to
    msg["Reply-To"] = config.BLB_FROM_EMAIL
    msg["Subject"] = subject
    msg.set_content(body)
    # Bounded: SMTP_TIMEOUT covers connect AND every socket op (a Gmail hang must
    # fail the one send, never stall the whole hourly outreach window).
    with smtplib.SMTP_SSL(account.get("smtp_host", "smtp.gmail.com"), 465,
                          context=ssl.create_default_context(),
                          timeout=SMTP_TIMEOUT) as s:
        s.login(account["from"], account["app_password"])
        s.send_message(msg)


def _login_probe(account: dict) -> None:
    """Auth-only probe: open SMTP/SSL, log in, hang up — proves *account*'s creds
    without sending. Bounded by :data:`SMTP_TIMEOUT` like the real send path."""
    import smtplib
    import ssl

    with smtplib.SMTP_SSL(account.get("smtp_host", "smtp.gmail.com"), 465,
                          context=ssl.create_default_context(),
                          timeout=SMTP_TIMEOUT) as s:
        s.login(account["from"], account["app_password"])


def verify(*, probe_fn=None) -> dict:
    """Probe SMTP auth WITHOUT sending (login then disconnect). Returns ``{ok, gated?, error?}``;
    never raises. ``com.utah.mailcheck`` calls this and pages the phone the instant auth breaks.
    Probes the FIRST account of the configured pool (the gmail.json-only probe reported a
    confusing 'No such file' while pool sends were actually working). A pool-wide
    verify_all stays a clean follow-up."""
    if probe_fn is None and not creds_available():
        return {"ok": False, "gated": True}
    if probe_fn is None:
        pool = accounts()
        if not pool:                                    # raced away since the gate check
            return {"ok": False, "gated": True}

        def prober() -> None:
            _login_probe(pool[0])
    else:
        prober = probe_fn
    try:
        prober()
        return {"ok": True, "gated": False}
    except Exception as exc:  # noqa: BLE001 — boundary fn: report, never raise
        return {"ok": False, "gated": False, "error": str(exc)}


def send(to: str, subject: str, body: str, *, send_fn=None) -> dict:
    """Send an email. With an injected ``send_fn`` it uses that; else it rotates across the
    account pool and sends via real SMTP; with no creds it documents the gate and returns
    ``sent=False`` (never fabricates). Never raises. Result carries the ``from`` account used."""
    # Header-injection gate (CWE-93): CR/LF in a header value smuggles extra headers
    # (one poisoned recipient = an arbitrary Bcc blast). Rejected before ANY adapter
    # runs and before a rotation slot is burned. Bodies may be multi-line; headers not.
    if not to.strip() or _header_unsafe(to) or _header_unsafe(subject):
        failures.record("mail", "header_rejected",
                        f"send to {to[:40]!r} rejected: blank or CR/LF/NUL in header")
        return {"sent": False, "gated": False, "to": to,
                "error": "header rejected: blank recipient or CR/LF in recipient/subject"}
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


__all__ = ["send", "verify", "creds_available", "accounts", "inboxes_exhausted",
           "sends_remaining", "GMAIL_CREDS", "ACCOUNTS_FILE", "ROTATION_STATE"]
