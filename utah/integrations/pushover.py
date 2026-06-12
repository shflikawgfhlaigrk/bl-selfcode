"""Pushover capability — phone push alerts (Ace's phone-notifier transitions HERE,
not an agent). Real transport: HTTPS POST to api.pushover.net.

Honest gate: with no creds it records the gate and returns ``sent=False, gated=True``
— it NEVER fakes a send. Emergency priority (2) carries ``retry``/``expire`` so the
phone re-alerts until acked. Every send failure is documented to the failure log.

Creds live OUTSIDE the repo at ``~/.utah/secrets/pushover.json`` (Michael's input):
``{api_token, user_key, group_key?, default_target?, encryption_key?}``.
If the Pushover app has end-to-end encryption enabled, ``encryption_key`` must be
the 64-char hex key from the app or every notification shows "error decrypting".
The HTTP transport is
injectable (``http_post=`` per call, or module-global :func:`set_transport`) so the
whole stack is testable with zero network and zero real pushes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac as hmac_mod
import json
import logging
import os
import subprocess
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from utah import config, failures
from utah.daemon import runtime

log = logging.getLogger("utah.integrations.pushover")

SECRET = runtime.UTAH_HOME / "secrets" / "pushover.json"
API_URL = "https://api.pushover.net/1/messages.json"

#: Pushover hard limits (we clamp to stay well inside them).
_MSG_MAX, _TITLE_MAX, _URL_MAX, _URLT_MAX = 1024, 250, 512, 100

_transport = None  # injected HTTP poster (tests); None => real urllib


def set_transport(fn) -> None:
    """Inject the HTTP poster (tests). ``None`` restores the real urllib transport."""
    global _transport
    _transport = fn


def _normalize_creds(raw: dict) -> dict:
    """Accept legacy Ace/plan key names (``token``/``user``) alongside Utah's
    ``api_token``/``user_key`` — same secret file, either shape works."""
    c = dict(raw)
    if not c.get("api_token") and c.get("token"):
        c["api_token"] = c["token"]
    if not c.get("user_key") and c.get("user"):
        c["user_key"] = c["user"]
    if not c.get("encryption_key"):
        for alias in ("e2ee_key", "e2e_key", "e2ee"):
            if c.get(alias):
                c["encryption_key"] = c[alias]
                break
    return c


def _encryption_key_hex(creds: dict) -> str | None:
    key = (creds.get("encryption_key") or "").strip().lower()
    if not key:
        return None
    if len(key) != 64 or any(ch not in "0123456789abcdef" for ch in key):
        log.warning("pushover encryption_key ignored: expected 64 hex chars")
        return None
    return key


#: Bound on the gzip child in the E2EE path — an unbounded subprocess on the ALERT
#: path could hang every page to the phone.
_GZIP_TIMEOUT_S = float(os.environ.get("UTAH_PUSHOVER_GZIP_TIMEOUT_S", "10"))


def encrypt_field(plaintext: str, key_hex: str, *, iv: bytes | None = None) -> str:
    """Pushover E2EE field encryption (gzip → AES-256-CBC → HMAC-SHA256 → base64)."""
    key = bytes.fromhex(key_hex)
    # Match Pushover's documented openssl pipeline (macOS/BSD gzip -9 -n) — the
    # byte-exact contract is pinned against the real openssl reference in tests.
    compressed = subprocess.check_output(
        ["gzip", "-9", "-c", "-n"], input=(plaintext or "").encode("utf-8"),
        timeout=_GZIP_TIMEOUT_S)
    iv_bytes = iv if iv is not None else os.urandom(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(compressed) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv_bytes))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    digest = hmac_mod.new(key, iv_bytes + ciphertext, hashlib.sha256).digest()
    return base64.b64encode(iv_bytes + ciphertext + digest).decode("ascii")


def _apply_e2ee(fields: dict, creds: dict) -> dict:
    """Encrypt the displayable fields, ALL or NOTHING: a mid-pipeline failure (gzip
    hang/missing) falls back to the plaintext fields — same trade as an invalid key
    (better a readable push than 'error decrypting' noise or a dead alert path),
    and a half-encrypted payload with the ``encrypted`` flag must never ship."""
    key_hex = _encryption_key_hex(creds)
    if not key_hex:
        return fields
    out = dict(fields)
    try:
        for name in ("message", "title", "url", "url_title"):
            if name in out and out[name] not in (None, ""):
                out[name] = encrypt_field(str(out[name]), key_hex)
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        failures.record("pushover", "e2ee_failed", f"sending plaintext: {exc}")
        return fields
    out["encrypted"] = "1"
    return out


def _load_creds() -> dict | None:
    try:
        c = json.loads(SECRET.read_text())
        return _normalize_creds(c) if isinstance(c, dict) else None
    except Exception:  # noqa: BLE001 — missing/garbled creds is a gate, not a crash
        return None


def available() -> bool:
    """True when creds are present (token + at least one recipient key)."""
    c = _load_creds()
    return bool(c and c.get("api_token") and (c.get("user_key") or c.get("group_key")))


def _resolve_target(creds: dict, target: str | None) -> str | None:
    """Map a logical target to a real recipient key. ``user``→personal, ``group``→
    delivery group, anything else is treated as a literal key. Falls back across the
    two so a missing one degrades instead of dropping the alert."""
    if not target or target == "user":
        return creds.get("user_key") or creds.get("group_key")
    if target == "group":
        return creds.get("group_key") or creds.get("user_key")
    return target


def _real_http_post(url: str, fields: dict, timeout: float = 10.0):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — fixed https URL
        return r.status, r.read().decode("utf-8", "replace")


def send(message: str, *, title: str = "Utah", priority: int = 0,
         target: str | None = None, url: str | None = None, url_title: str | None = None,
         retry: int | None = None, expire: int | None = None, http_post=None) -> dict:
    """Push *message* to the phone via Pushover. Honest gate (no creds → ``gated``),
    never fakes, never raises. Returns a dict describing the outcome."""
    if not config.PUSHOVER_ENABLED:
        return {"sent": False, "gated": True, "reason": "disabled"}

    creds = _load_creds()
    if not creds or not creds.get("api_token"):
        failures.record("pushover", "gated", f"phone push gated: no creds ({SECRET})")
        return {"sent": False, "gated": True, "reason": "no_creds"}

    to = _resolve_target(creds, target or creds.get("default_target") or config.PUSHOVER_DEFAULT_TARGET)
    if not to:
        failures.record("pushover", "gated", "phone push gated: no user_key/group_key in creds")
        return {"sent": False, "gated": True, "reason": "no_target"}

    fields = {
        "token": creds["api_token"],
        "user": to,
        "message": (message or "")[:_MSG_MAX] or "(empty)",
        "title": (title or "Utah")[:_TITLE_MAX],
        "priority": int(priority),
    }
    if url:
        fields["url"] = url[:_URL_MAX]
    if url_title:
        fields["url_title"] = url_title[:_URLT_MAX]
    if int(priority) >= 2:  # emergency: keep re-alerting until acked
        fields["retry"] = int(retry or config.PUSHOVER_EMERGENCY_RETRY)
        fields["expire"] = int(expire or config.PUSHOVER_EMERGENCY_EXPIRE)

    fields = _apply_e2ee(fields, creds)

    poster = http_post or _transport or _real_http_post
    try:
        status, body = poster(API_URL, fields)
    except Exception as exc:  # noqa: BLE001 — network/transport failure
        failures.record("pushover", "send_failed", str(exc))
        return {"sent": False, "gated": False, "error": str(exc)}

    try:
        ok = int(status) == 200 and json.loads(body).get("status") == 1
    except Exception:  # noqa: BLE001 — unparseable body counts as failure
        ok = False
    if not ok:
        failures.record("pushover", "send_failed", f"HTTP {status}: {str(body)[:300]}")
        return {"sent": False, "gated": False, "status": status, "body": str(body)[:300]}

    return {"sent": True, "gated": False, "priority": int(priority),
            "target": (to[:6] + "…") if len(to) > 6 else to}


__all__ = ["send", "available", "set_transport", "encrypt_field", "SECRET", "API_URL"]
