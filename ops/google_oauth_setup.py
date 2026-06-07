#!/usr/bin/env python3
"""One-time Google OAuth setup → writes ~/.utah/secrets/google.json.

Installed-app (Desktop) loopback flow, STDLIB ONLY (no pip deps). It opens the
Google consent screen, you click Allow, it captures the auth code on a localhost
redirect, exchanges it for a **refresh token**, and writes the creds file Utah's
calendar (and, if you add the gmail scope, the Gmail-API mail path) reads.

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
"""
from __future__ import annotations

import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

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


def main() -> int:
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET") or input(
        "Paste the Google OAuth client secret (stays local, not echoed to chat): "
    ).strip()
    if not client_secret:
        print("no client secret — aborting", file=sys.stderr)
        return 2

    captured: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            captured.update(
                {k: v[0] for k, v in urllib.parse.parse_qs(
                    urllib.parse.urlparse(self.path).query).items()}
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Google auth complete. You can close this tab and return to the terminal.")

        def log_message(self, *a):  # silence
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/"
    state = secrets.token_urlsafe(16)

    auth = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",        # forces a refresh_token even on re-grant
        "state": state,
    })
    print("\nScopes requested:", " ".join(SCOPES))
    print("\nOpen this URL and click Allow (it should open automatically):\n")
    print(auth, "\n")
    try:
        webbrowser.open(auth)
    except Exception:
        pass

    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    for _ in range(int(os.environ.get("UTAH_OAUTH_WAIT", "300"))):  # default 5 min to approve
        if captured.get("code") or captured.get("error"):
            break
        time.sleep(1)
    srv.server_close()

    if captured.get("error"):
        print("consent error:", captured["error"], file=sys.stderr)
        return 1
    if captured.get("state") != state or not captured.get("code"):
        print("no valid auth code captured (timed out or state mismatch)", file=sys.stderr)
        return 1

    data = urllib.parse.urlencode({
        "code": captured["code"],
        "client_id": CLIENT_ID,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=data)) as r:
            tok = json.load(r)
    except urllib.error.HTTPError as e:
        print("token exchange failed:", e.read().decode()[:300], file=sys.stderr)
        return 1

    refresh = tok.get("refresh_token")
    if not refresh:
        print("no refresh_token returned (revoke prior grant or check 'offline' access):",
              json.dumps(tok)[:200], file=sys.stderr)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "client_id": CLIENT_ID,
        "client_secret": client_secret,
        "refresh_token": refresh,
        "scopes": SCOPES,
    }, indent=2))
    os.chmod(OUT, 0o600)
    print(f"\n✅ wrote {OUT} (mode 600) — calendar capability is now credentialed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
