"""OAuth Keychain adapter — Utah namespace (``com.utah.oauth``), stdlib only.

Reads/refreshes Gmail OAuth blobs the same way Ace's capstone gateway does, but under
Utah's own Keychain service. Ace's live token may still be on the typo account
(``mthburnsbarber``); :func:`gmail_profile` exposes that so :mod:`utah.auth_repair`
can re-consent autonomously for :data:`utah.config.OWNER_EMAIL`.
"""
from __future__ import annotations

import json
import logging
import platform
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from utah import config

log = logging.getLogger("utah.integrations.oauth")

UTAH_SERVICE = "com.utah.oauth"
ACE_SERVICE = "com.ace.oauth"
GMAIL_ACCOUNT = "gmail"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_REFRESH_SKEW = timedelta(seconds=60)


class OAuthError(RuntimeError):
    """Missing/dead OAuth token or API failure."""


def _security(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/usr/bin/security", *args],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )


def read_blob(*, service: str = UTAH_SERVICE, account: str = GMAIL_ACCOUNT) -> dict | None:
    if platform.system() != "Darwin":
        return None
    p = _security("find-generic-password", "-s", service, "-a", account, "-w")
    if p.returncode != 0:
        return None
    raw = (p.stdout or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OAuthError(f"corrupt OAuth blob ({service}/{account}): {exc}") from exc


def write_blob(blob: dict, *, service: str = UTAH_SERVICE, account: str = GMAIL_ACCOUNT) -> None:
    if platform.system() != "Darwin":
        return
    payload = json.dumps(blob, separators=(",", ":"))
    _security(
        "add-generic-password", "-U",
        "-s", service, "-a", account, "-w", payload,
    )


def _expired(blob: dict) -> bool:
    if not blob.get("token") and not blob.get("access_token"):
        return True
    exp = blob.get("expiry")
    if not exp:
        return True
    try:
        dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
    except ValueError:
        return True
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= dt - _REFRESH_SKEW


def _post_form(url: str, fields: dict) -> tuple[int, Any]:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body


def refresh_blob(blob: dict) -> dict:
    rt = blob.get("refresh_token")
    if not rt:
        raise OAuthError("OAuth blob has no refresh_token")
    cid = blob.get("client_id") or ""
    secret = blob.get("client_secret") or ""
    if not cid or not secret:
        gpath = config.UTAH_HOME / "secrets" / "google.json"
        if gpath.is_file():
            g = json.loads(gpath.read_text(encoding="utf-8"))
            cid = cid or g.get("client_id", "")
            secret = secret or g.get("client_secret", "")
    status, payload = _post_form(TOKEN_URL, {
        "client_id": cid,
        "client_secret": secret,
        "refresh_token": rt,
        "grant_type": "refresh_token",
    })
    if status != 200 or not isinstance(payload, dict):
        low = json.dumps(payload).lower() if isinstance(payload, dict) else str(payload).lower()
        if "invalid_grant" in low:
            raise OAuthError("refresh token rejected (invalid_grant)")
        raise OAuthError(f"token refresh failed ({status}): {str(payload)[:200]}")
    blob["token"] = payload["access_token"]
    blob["client_id"] = cid
    blob["client_secret"] = secret
    ttl = int(payload.get("expires_in", 3600))
    blob["expiry"] = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
    if payload.get("refresh_token"):
        blob["refresh_token"] = payload["refresh_token"]
    return blob


def access_token(blob: dict) -> str:
    if _expired(blob):
        blob = refresh_blob(blob)
    tok = blob.get("token") or blob.get("access_token")
    if not tok:
        raise OAuthError("no access token after refresh")
    return tok


def gmail_profile(*, service: str = UTAH_SERVICE) -> dict:
    """Live Gmail profile for *service* — proves token end-to-end."""
    blob = read_blob(service=service)
    if not blob:
        raise OAuthError(f"no OAuth token ({service}/{GMAIL_ACCOUNT})")
    if _expired(blob):
        blob = refresh_blob(blob)
        write_blob(blob, service=service)
    tok = blob.get("token") or blob.get("access_token")
    if not tok:
        raise OAuthError("no access token after refresh")
    req = urllib.request.Request(
        f"{GMAIL_BASE}/profile",
        headers={"Authorization": f"Bearer {tok}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            p = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise OAuthError(f"profile failed: {exc.code}") from exc
    email = config.normalize_owner_email(p.get("emailAddress"))
    return {
        "email": email,
        "service": service,
        "messages_total": p.get("messagesTotal"),
        "threads_total": p.get("threadsTotal"),
    }


def ace_gmail_email() -> str | None:
    try:
        return gmail_profile(service=ACE_SERVICE)["email"]
    except OAuthError:
        return None


def utah_gmail_email() -> str | None:
    try:
        return gmail_profile(service=UTAH_SERVICE)["email"]
    except OAuthError:
        return None


def copy_ace_to_utah_if_correct() -> bool:
    """One-time copy when Ace's token is already on :data:`OWNER_EMAIL`."""
    blob = read_blob(service=ACE_SERVICE)
    if not blob:
        return False
    try:
        email = gmail_profile(service=ACE_SERVICE)["email"]
    except OAuthError:
        return False
    if email.lower() != config.OWNER_EMAIL.lower():
        return False
    write_blob(blob, service=UTAH_SERVICE)
    log.info("oauth: copied Ace gmail token → com.utah.oauth (%s)", email)
    return True


__all__ = [
    "OAuthError",
    "UTAH_SERVICE",
    "ACE_SERVICE",
    "read_blob",
    "write_blob",
    "gmail_profile",
    "ace_gmail_email",
    "utah_gmail_email",
    "copy_ace_to_utah_if_correct",
    "refresh_blob",
]
