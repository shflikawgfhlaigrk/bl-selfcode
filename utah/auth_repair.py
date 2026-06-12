"""Autonomous auth repair — Ace fixes Gmail/OAuth, Michael only taps 2FA.

When Ace's Keychain OAuth is on the typo account (``mthburnsbarber``) or SMTP rejects,
this module normalizes creds, opens Chrome on the **chrome-ace** profile with
``login_hint=mtuburnsbarber@gmail.com``, and starts OAuth re-consent in the background.
Never hands Michael JSON or "sign in as X" instructions.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from utah import config
from utah.daemon import runtime
from utah.integrations import oauth

log = logging.getLogger("utah.auth_repair")

#: The Google-auth Chrome profile lives under ~/.utah now (binding rule M: the runtime
#: never reads ~/.ace). The legacy ~/.ace/chrome-ace login is forward-migrated ONCE.
WC_CHROME = runtime.UTAH_HOME / "chrome-ace"
_CHROME_LEGACY = Path.home() / ".ace" / "chrome-ace"


def _migrate_chrome_once() -> None:
    """Copy the logged-in Ace Google Chrome profile into ~/.utah exactly once (iff the
    ~/.utah profile is absent and the legacy exists). Best-effort; never raises."""
    import shutil

    try:
        if not WC_CHROME.is_dir() and _CHROME_LEGACY.is_dir():
            shutil.copytree(_CHROME_LEGACY, WC_CHROME, dirs_exist_ok=False,
                            ignore=shutil.ignore_patterns("Singleton*", "*.lock"))
            log.info("auth_repair: migrated chrome profile %s → %s (one-time)",
                     _CHROME_LEGACY, WC_CHROME)
    except (OSError, shutil.Error) as exc:  # a copy hiccup just means Michael re-logs in
        log.debug("auth_repair: chrome profile migrate skipped: %s", exc)


REPAIR_STATE = runtime.RUN_DIR / "auth_repair.json"
GMAIL_CREDS = runtime.UTAH_HOME / "secrets" / "gmail.json"
GOOGLE_CREDS = runtime.UTAH_HOME / "secrets" / "google.json"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]
_REPAIR_COOLDOWN_S = 1800  # don't spam OAuth/browser more than every 30 min


def _load_state() -> dict:
    try:
        return json.loads(REPAIR_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _save_state(data: dict) -> None:
    try:
        runtime.RUN_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        REPAIR_STATE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        log.debug("auth_repair state write failed: %s", exc)


def _recently_attempted(kind: str) -> bool:
    st = _load_state()
    last = float(st.get(kind, 0) or 0)
    return (time.time() - last) < _REPAIR_COOLDOWN_S


def _mark_attempt(kind: str) -> None:
    st = _load_state()
    st[kind] = time.time()
    _save_state(st)


def url_with_login_hint(base: str, **params: str) -> str:
    q = dict(params)
    q.setdefault("login_hint", config.OWNER_EMAIL)
    q.setdefault("prompt", "select_account")
    sep = "&" if "?" in base else "?"
    return base + sep + urllib.parse.urlencode(q)


def open_chrome_ace(url: str) -> bool:
    """Open *url* in Ace's logged-in Chrome profile — not a blank default browser."""
    chrome = None
    try:
        from utah.integrations import browser

        chrome = browser.chrome_binary()
    except (ImportError, OSError) as exc:  # no browser module/binary → `open` fallback below
        log.debug("auth_repair: chrome_binary unavailable: %s", exc)
    _migrate_chrome_once()
    if chrome and WC_CHROME.is_dir():
        try:
            subprocess.Popen(
                [chrome, f"--user-data-dir={WC_CHROME}", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            log.info("auth_repair: opened chrome profile → %s", url[:80])
            return True
        except OSError as exc:
            log.warning("auth_repair chrome launch failed: %s", exc)
    try:
        # `-g`: background open — never steal Michael's focus for a repair prompt.
        subprocess.run(["open", "-g", url], check=False, timeout=5)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def normalize_gmail_json() -> bool:
    """Fix ``from`` typo in gmail.json in place."""
    if not GMAIL_CREDS.is_file():
        return False
    try:
        data = json.loads(GMAIL_CREDS.read_text(encoding="utf-8"))
        # Both known bad shapes: the mthburnsbarber owner typo AND legacy BLB
        # storefront addresses (delivery@/daily@ → the canonical BLB From).
        fixed = config.normalize_blb_from_email(
            config.normalize_owner_email(data.get("from")))
        if fixed and fixed != data.get("from"):
            data["from"] = fixed
            GMAIL_CREDS.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            GMAIL_CREDS.chmod(0o600)
            log.info("auth_repair: fixed gmail.json from → %s", fixed)
            return True
    except (OSError, json.JSONDecodeError) as exc:
        log.debug("normalize_gmail_json: %s", exc)
    return False


def diagnose_gmail(*, mail_verify=None) -> dict[str, Any]:
    from utah import mail

    verify = mail_verify or mail.verify
    smtp = verify()
    ace_email = oauth.ace_gmail_email()
    utah_email = oauth.utah_gmail_email()
    owner = config.OWNER_EMAIL.lower()
    return {
        "smtp": smtp,
        "ace_oauth_email": ace_email,
        "utah_oauth_email": utah_email,
        "owner_email": config.OWNER_EMAIL,
        "ace_oauth_wrong": bool(ace_email and ace_email.lower() != owner),
        "utah_oauth_wrong": bool(utah_email and utah_email.lower() != owner),
        "oauth_missing": ace_email is None and utah_email is None,
    }


def _oauth_loopback_consent(*, scopes: list[str] | None = None) -> dict[str, Any]:
    """Background desktop OAuth → writes ``com.utah.oauth/gmail``. Non-blocking start."""
    import http.server

    scopes = scopes or GMAIL_SCOPES
    if not GOOGLE_CREDS.is_file():
        return {"ok": False, "error": "no google.json client creds"}
    try:
        g = json.loads(GOOGLE_CREDS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("auth_repair: google.json unreadable: %s", exc)
        return {"ok": False, "error": f"google.json unreadable: {exc}"}
    if not isinstance(g, dict):
        return {"ok": False, "error": "google.json is not a JSON object"}
    client_id = g.get("client_id", "")
    client_secret = g.get("client_secret", "")
    if not (client_id and client_secret):
        return {"ok": False, "error": "google.json missing client_id/secret"}

    captured: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            captured.update({
                k: v[0]
                for k, v in urllib.parse.parse_qs(
                    urllib.parse.urlparse(self.path).query,
                ).items()
            })
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Ace OAuth complete - you can close this tab.")

        def log_message(self, *a):  # silence
            pass

    def _run():
        srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        port = srv.server_address[1]
        redirect = f"http://127.0.0.1:{port}/"
        state = secrets.token_urlsafe(16)
        auth = AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": " ".join(scopes),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "login_hint": config.OWNER_EMAIL,
        })
        open_chrome_ace(auth)
        t = threading.Thread(target=srv.handle_request, daemon=True)
        t.start()
        deadline = time.time() + 300
        while time.time() < deadline and not (captured.get("code") or captured.get("error")):
            time.sleep(0.5)
        srv.server_close()
        if captured.get("error") or captured.get("state") != state or not captured.get("code"):
            log.warning("auth_repair oauth: no code (%s)", captured.get("error", "timeout"))
            return
        data = urllib.parse.urlencode({
            "code": captured["code"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
        }).encode()
        try:
            with urllib.request.urlopen(
                urllib.request.Request(TOKEN_URL, data=data, method="POST"),
                timeout=20,
            ) as resp:
                tok = json.loads(resp.read().decode())
        except (urllib.error.URLError, OSError, http.client.HTTPException,
                ValueError) as exc:  # URLError ⊂ OSError; ValueError = bad JSON body
            log.warning("auth_repair oauth token exchange failed: %s", exc)
            return
        refresh = tok.get("refresh_token")
        if not refresh:
            log.warning("auth_repair oauth: no refresh_token in response")
            return
        blob = {
            "token": tok.get("access_token"),
            "refresh_token": refresh,
            "client_id": client_id,
            "client_secret": client_secret,
            "scopes": scopes,
            "expiry": "",
        }
        oauth.write_blob(blob, service=oauth.UTAH_SERVICE)
        log.info("auth_repair: wrote com.utah.oauth/gmail for %s", config.OWNER_EMAIL)

    threading.Thread(target=_run, daemon=True, name="utah-oauth-repair").start()
    return {"ok": True, "started": True, "login_hint": config.OWNER_EMAIL}


def repair_gmail_oauth(*, force: bool = False) -> dict[str, Any]:
    """Re-consent OAuth on the correct account when Ace's token is on the typo."""
    diag = diagnose_gmail()
    wrong = diag["ace_oauth_wrong"] or diag["utah_oauth_wrong"] or diag["oauth_missing"]
    if not wrong and not force:
        if oauth.copy_ace_to_utah_if_correct():
            return {"action": "copied_ace_oauth", "ok": True}
        return {"action": "noop", "ok": True, "reason": "oauth already correct"}

    if _recently_attempted("oauth_repair") and not force:
        return {"action": "oauth_repair_skipped", "ok": False, "reason": "cooldown"}

    _mark_attempt("oauth_repair")
    if oauth.copy_ace_to_utah_if_correct():
        return {"action": "copied_ace_oauth", "ok": True}

    started = _oauth_loopback_consent()
    return {"action": "oauth_reconsent_started", **started, "diag": diag}


def repair_gmail_smtp(*, open_browser: bool = True) -> dict[str, Any]:
    """Normalize gmail.json + open app-passwords on chrome-ace if SMTP still fails."""
    from utah import mail

    normalize_gmail_json()
    mv = mail.verify()
    if mv.get("ok"):
        return {"action": "smtp_ok", "ok": True, "mail": mv}

    if open_browser and not _recently_attempted("smtp_repair"):
        _mark_attempt("smtp_repair")
        url = url_with_login_hint("https://myaccount.google.com/apppasswords")
        open_chrome_ace(url)
        return {"action": "opened_app_passwords", "ok": False, "mail": mv, "url": url}

    return {"action": "smtp_still_failing", "ok": False, "mail": mv}


def repair_gmail(*, notify_2fa_fn=None) -> dict[str, Any]:
    """Full autonomous Gmail repair sweep. Returns a report; never raises."""
    from utah import secrets_sync

    report: dict[str, Any] = {"steps": []}
    normalize_gmail_json()
    report["steps"].append("normalize_gmail_json")
    secrets_sync.sync_business(write=True)
    report["steps"].append("secrets_sync")

    smtp = repair_gmail_smtp()
    report["smtp"] = smtp
    oauth_result = repair_gmail_oauth()
    report["oauth"] = oauth_result
    report["diag"] = diagnose_gmail()
    utah_oauth_email = (report["diag"].get("utah_oauth_email") or "").lower()
    report["ok"] = bool(smtp.get("ok")) or utah_oauth_email == config.OWNER_EMAIL.lower()

    needs_2fa = (
        oauth_result.get("started")
        or smtp.get("action") == "opened_app_passwords"
    ) and not report["ok"]
    if needs_2fa and notify_2fa_fn:
        notify_2fa_fn(
            "Ace opened Google on chrome-ace for "
            f"{config.OWNER_EMAIL} — tap 2FA on your phone if it prompts. "
            "Ace picks up the new token automatically.",
        )
    report["needs_2fa"] = needs_2fa
    return report


__all__ = [
    "diagnose_gmail",
    "repair_gmail",
    "repair_gmail_oauth",
    "repair_gmail_smtp",
    "open_chrome_ace",
    "url_with_login_hint",
    "normalize_gmail_json",
]
