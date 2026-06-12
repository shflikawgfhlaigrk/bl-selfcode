#!/usr/bin/env python3
"""One-time Google OAuth setup → writes ~/.utah/secrets/google.json.

Installed-app (Desktop) loopback flow, STDLIB ONLY (no pip deps). It opens the
Google consent screen, you click Allow, it captures the auth code on a localhost
redirect, exchanges it for a **refresh token**, and writes the creds file Utah's
calendar (and, if you add the gmail scope, the Gmail-API mail path) reads.

Every step is an isolated function returning an honest ``{"ok": bool, ...}`` dict
(testable with injected transport/clock — no real network in tests), and every
network call carries an explicit timeout (``UTAH_OAUTH_HTTP_TIMEOUT``, default 30s).

PREREQS
  * The OAuth client must be type **"Desktop app"** (Google then auto-allows the
    http://127.0.0.1:<port> loopback redirect — no redirect URI to register).
  * You provide the **client secret** locally (env var or prompt) — never in chat.

RUN
    cd ~/Desktop/ProjectUtah
    GOOGLE_CLIENT_SECRET='paste-secret-here' ~/.utah/venv/bin/python ops/google_oauth_setup.py
  (or just run it and paste the secret when prompted)

SCOPES (edit SCOPES below, or set GOOGLE_SCOPES space-separated):
    calendar.events  -> Utah daemon creates calendar events
    gmail.send       -> (optional) lets us power Gmail via the API instead of SMTP

Outbound BLB mail uses info@blacklabelbots.com (see utah.config.BLB_FROM_EMAIL). If you
enable gmail.send here, authorize that address in Google Workspace (Send mail as) or OAuth
will send from the logged-in account only.
"""
from __future__ import annotations

import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable, Mapping

CLIENT_ID = os.environ.get(
    "GOOGLE_CLIENT_ID",
    "1097975047347-vplioo3ukj28osho5if11tn1v4cks3fh.apps.googleusercontent.com",
)
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    # "https://www.googleapis.com/auth/gmail.send",   # uncomment to also power Gmail via API
]
SCOPES = (os.environ.get("GOOGLE_SCOPES") or " ".join(DEFAULT_SCOPES)).split()
OUT = Path.home() / ".utah" / "secrets" / "google.json"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

#: Explicit bound on the token-exchange HTTP call — an unbounded urlopen hung the
#: whole setup forever when the token endpoint stalled.
HTTP_TIMEOUT_S = float(os.environ.get("UTAH_OAUTH_HTTP_TIMEOUT", "30"))


# ---------------------------------------------------------------------------
# step 1: client secret (env first, local prompt fallback)
# ---------------------------------------------------------------------------

def get_client_secret(env: Mapping[str, str] | None = None,
                      prompt_fn: Callable[[str], str] = input) -> dict:
    """``{"ok", "secret", "error"}`` — never raises (EOF/interrupt = honest failure)."""
    env = os.environ if env is None else env
    secret = (env.get("GOOGLE_CLIENT_SECRET") or "").strip()
    if not secret:
        try:
            secret = prompt_fn(
                "Paste the Google OAuth client secret (stays local, not echoed to chat): "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            return {"ok": False, "secret": None, "error": "no client secret provided"}
    if not secret:
        return {"ok": False, "secret": None, "error": "empty client secret"}
    return {"ok": True, "secret": secret, "error": None}


# ---------------------------------------------------------------------------
# step 2: loopback redirect server (binds 127.0.0.1, OS-assigned port)
# ---------------------------------------------------------------------------

def start_loopback(host: str = "127.0.0.1", port: int = 0) -> dict:
    """``{"ok", "server", "captured", "redirect_uri", "error"}``. The handler folds each
    request's query params into ``captured`` (first writer wins, so a stray favicon or a
    replayed tab cannot clobber the real code); the caller polls ``captured``."""
    captured: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            for k, v in params.items():
                captured.setdefault(k, v[0])
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(
                b"Google auth complete. You can close this tab and return to the terminal.")

        def log_message(self, *a):  # silence
            pass

    try:
        srv = http.server.HTTPServer((host, port), Handler)
    except OSError as exc:
        return {"ok": False, "server": None, "captured": captured,
                "redirect_uri": None, "error": f"cannot bind loopback server: {exc}"}
    srv.timeout = 1.0  # handle_request wakes every second so the serve loop can stop
    return {"ok": True, "server": srv, "captured": captured,
            "redirect_uri": f"http://{host}:{srv.server_address[1]}/", "error": None}


def _serve_until_captured(srv: http.server.HTTPServer, captured: dict, deadline: float,
                          clock: Callable[[], float] = time.monotonic) -> None:
    """Serve loopback requests (favicon probes included) until the redirect lands or the
    deadline passes. Runs on a daemon thread; ``srv.timeout`` bounds each iteration."""
    while clock() < deadline and not (captured.get("code") or captured.get("error")):
        try:
            srv.handle_request()
        except OSError:  # server_close() raced us — we are done either way
            return


# ---------------------------------------------------------------------------
# step 3: consent URL (pure) + browser nudge
# ---------------------------------------------------------------------------

def build_auth_url(redirect_uri: str, state: str, *, client_id: str = CLIENT_ID,
                   scopes: list[str] | None = None) -> str:
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES if scopes is None else scopes),
        "access_type": "offline",
        "prompt": "consent",        # forces a refresh_token even on re-grant
        "state": state,
    })


def open_browser(url: str) -> bool:
    """Best-effort: the URL is also printed, so a refused browser is not fatal."""
    try:
        return webbrowser.open(url)
    except (webbrowser.Error, OSError):
        return False


# ---------------------------------------------------------------------------
# step 4: wait for the redirect (bounded, injectable clock — no busy hang)
# ---------------------------------------------------------------------------

def wait_for_code(captured: Mapping[str, str], state: str, wait_s: float = 300.0,
                  sleep_fn: Callable[[float], None] = time.sleep,
                  clock: Callable[[], float] = time.monotonic) -> dict:
    """``{"ok", "code", "error"}`` — consent error, timeout, and a state (CSRF)
    mismatch are all honest failures, never exceptions."""
    deadline = clock() + wait_s
    while not (captured.get("code") or captured.get("error")):
        if clock() >= deadline:
            return {"ok": False, "code": None,
                    "error": f"timed out after {wait_s:.0f}s waiting for the consent redirect"}
        sleep_fn(0.25)
    if captured.get("error"):
        return {"ok": False, "code": None, "error": f"consent error: {captured['error']}"}
    if captured.get("state") != state:
        return {"ok": False, "code": None,
                "error": "state mismatch on the consent redirect (stale tab or CSRF) — re-run"}
    return {"ok": True, "code": captured["code"], "error": None}


# ---------------------------------------------------------------------------
# step 5: token exchange (the only off-box network call — explicit timeout,
#          injectable transport)
# ---------------------------------------------------------------------------

def exchange_code(code: str, client_secret: str, redirect_uri: str, *,
                  client_id: str = CLIENT_ID, token_url: str = TOKEN_URL,
                  opener: Callable | None = None,
                  timeout_s: float = HTTP_TIMEOUT_S) -> dict:
    """``{"ok", "tokens", "error"}`` — never raises. *opener* takes
    ``(request, timeout=...)`` and returns a context manager (urlopen-shaped)."""
    opener = urllib.request.urlopen if opener is None else opener
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    try:
        with opener(urllib.request.Request(token_url, data=data), timeout=timeout_s) as r:
            body = r.read()
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode(errors="replace")[:300]
        except (OSError, AttributeError):
            detail = str(exc)
        return {"ok": False, "tokens": None,
                "error": f"token exchange failed: HTTP {exc.code}: {detail}"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "tokens": None, "error": f"token endpoint unreachable: {exc}"}
    try:
        tokens = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {"ok": False, "tokens": None, "error": f"token response is not JSON: {exc}"}
    if not isinstance(tokens, dict):
        return {"ok": False, "tokens": None, "error": "token response is not a JSON object"}
    if not tokens.get("refresh_token"):
        return {"ok": False, "tokens": tokens,
                "error": "no refresh_token returned (revoke prior grant or check "
                         "'offline' access): " + json.dumps(tokens)[:200]}
    return {"ok": True, "tokens": tokens, "error": None}


# ---------------------------------------------------------------------------
# step 6: write the creds file (0600)
# ---------------------------------------------------------------------------

def write_creds(client_secret: str, refresh_token: str, *, client_id: str = CLIENT_ID,
                scopes: list[str] | None = None, out_path: Path | str | None = None) -> dict:
    """``{"ok", "path", "error"}`` — never raises (full disk / bad perms = honest failure)."""
    out = OUT if out_path is None else Path(out_path)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "scopes": SCOPES if scopes is None else scopes,
        }, indent=2))
        os.chmod(out, 0o600)
    except OSError as exc:
        return {"ok": False, "path": str(out), "error": f"cannot write creds file: {exc}"}
    return {"ok": True, "path": str(out), "error": None}


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def main() -> int:
    sec = get_client_secret()
    if not sec["ok"]:
        print(sec["error"], "— aborting", file=sys.stderr)
        return 2

    boot = start_loopback()
    if not boot["ok"]:
        print(boot["error"], file=sys.stderr)
        return 1
    srv, captured = boot["server"], boot["captured"]
    state = secrets.token_urlsafe(16)
    auth = build_auth_url(boot["redirect_uri"], state)

    print("\nScopes requested:", " ".join(SCOPES))
    print("\nOpen this URL and click Allow (it should open automatically):\n")
    print(auth, "\n")
    open_browser(auth)

    wait_s = float(os.environ.get("UTAH_OAUTH_WAIT", "300"))  # default 5 min to approve
    deadline = time.monotonic() + wait_s
    threading.Thread(target=_serve_until_captured, args=(srv, captured, deadline),
                     daemon=True).start()
    got = wait_for_code(captured, state, wait_s)
    srv.server_close()
    if not got["ok"]:
        print(got["error"], file=sys.stderr)
        return 1

    ex = exchange_code(got["code"], sec["secret"], boot["redirect_uri"])
    if not ex["ok"]:
        print(ex["error"], file=sys.stderr)
        return 1

    wrote = write_creds(sec["secret"], ex["tokens"]["refresh_token"])
    if not wrote["ok"]:
        print(wrote["error"], file=sys.stderr)
        return 1
    print(f"\n✅ wrote {wrote['path']} (mode 600) — calendar capability is now credentialed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
