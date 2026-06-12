"""Google OAuth setup script — every step is an isolated, honest {"ok": ...} boundary
and every network call carries an explicit timeout. The token exchange is tested with an
INJECTED transport (no real network); the loopback capture is tested against 127.0.0.1
only. Pins: state/CSRF check, consent-error and timeout paths, refresh-token requirement,
creds file written 0600."""
from __future__ import annotations

import io
import json
import stat
import threading
import urllib.error
import urllib.parse
import urllib.request

from ops import google_oauth_setup as g


# ---------------------------------------------------------------------------
# client secret
# ---------------------------------------------------------------------------

def test_get_client_secret_env_wins_over_prompt():
    res = g.get_client_secret(env={"GOOGLE_CLIENT_SECRET": " s3cret "},
                              prompt_fn=lambda _: (_ for _ in ()).throw(AssertionError))
    assert res == {"ok": True, "secret": "s3cret", "error": None}


def test_get_client_secret_prompt_fallback_and_empty_is_honest():
    assert g.get_client_secret(env={}, prompt_fn=lambda _: "  typed  ")["secret"] == "typed"
    res = g.get_client_secret(env={}, prompt_fn=lambda _: "   ")
    assert res["ok"] is False and res["secret"] is None and "secret" in res["error"]


def test_get_client_secret_eof_on_prompt_is_honest_not_a_crash():
    def prompt(_):
        raise EOFError

    res = g.get_client_secret(env={}, prompt_fn=prompt)
    assert res["ok"] is False and res["secret"] is None


# ---------------------------------------------------------------------------
# auth URL (pure)
# ---------------------------------------------------------------------------

def test_build_auth_url_carries_offline_consent_state_and_scopes():
    url = g.build_auth_url("http://127.0.0.1:9/", "st4te",
                           client_id="cid", scopes=["a", "b"])
    assert url.startswith(g.AUTH_URL + "?")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert q == {
        "client_id": "cid",
        "redirect_uri": "http://127.0.0.1:9/",
        "response_type": "code",
        "scope": "a b",
        "access_type": "offline",
        "prompt": "consent",
        "state": "st4te",
    }


# ---------------------------------------------------------------------------
# loopback capture (127.0.0.1 only — no external network)
# ---------------------------------------------------------------------------

def test_loopback_server_captures_code_and_state():
    boot = g.start_loopback()
    assert boot["ok"] is True and boot["error"] is None
    srv, captured = boot["server"], boot["captured"]
    try:
        t = threading.Thread(target=srv.handle_request, daemon=True)
        t.start()
        with urllib.request.urlopen(boot["redirect_uri"] + "?code=abc&state=xyz",
                                    timeout=5) as r:
            body = r.read()
        t.join(timeout=5)
    finally:
        srv.server_close()
    assert captured.get("code") == "abc" and captured.get("state") == "xyz"
    assert b"close this tab" in body.lower()


# ---------------------------------------------------------------------------
# waiting for the redirect (injected clock — no real sleeping)
# ---------------------------------------------------------------------------

def _fake_clock(step=1.0):
    t = {"now": 0.0}

    def clock():
        t["now"] += step
        return t["now"]

    return clock


def test_wait_for_code_success_checks_state():
    res = g.wait_for_code({"code": "c", "state": "s"}, "s",
                          wait_s=10, sleep_fn=lambda _: None, clock=_fake_clock())
    assert res == {"ok": True, "code": "c", "error": None}


def test_wait_for_code_state_mismatch_is_rejected():
    res = g.wait_for_code({"code": "c", "state": "EVIL"}, "s",
                          wait_s=10, sleep_fn=lambda _: None, clock=_fake_clock())
    assert res["ok"] is False and res["code"] is None and "state" in res["error"]


def test_wait_for_code_consent_error_is_surfaced():
    res = g.wait_for_code({"error": "access_denied"}, "s",
                          wait_s=10, sleep_fn=lambda _: None, clock=_fake_clock())
    assert res["ok"] is False and "access_denied" in res["error"]


def test_wait_for_code_times_out_honestly():
    res = g.wait_for_code({}, "s", wait_s=3, sleep_fn=lambda _: None, clock=_fake_clock())
    assert res["ok"] is False and "timed out" in res["error"]


# ---------------------------------------------------------------------------
# token exchange (injected transport)
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener_returning(body: bytes, seen: dict):
    def opener(req, timeout):
        seen["url"] = req.full_url
        seen["data"] = req.data
        seen["timeout"] = timeout
        return _FakeResponse(body)

    return opener


def test_exchange_code_success_sends_grant_and_explicit_timeout():
    seen: dict = {}
    body = json.dumps({"refresh_token": "rt", "access_token": "at"}).encode()
    res = g.exchange_code("the-code", "sec", "http://127.0.0.1:9/",
                          client_id="cid", opener=_opener_returning(body, seen))
    assert res["ok"] is True and res["tokens"]["refresh_token"] == "rt"
    assert seen["url"] == g.TOKEN_URL
    assert seen["timeout"] == g.HTTP_TIMEOUT_S and seen["timeout"] > 0
    q = dict(urllib.parse.parse_qsl(seen["data"].decode()))
    assert q["grant_type"] == "authorization_code"
    assert q["code"] == "the-code" and q["client_secret"] == "sec"


def test_exchange_code_http_error_returns_honest_failure():
    def opener(req, timeout):
        raise urllib.error.HTTPError(
            g.TOKEN_URL, 400, "Bad Request", None, io.BytesIO(b'{"error":"invalid_grant"}'))

    res = g.exchange_code("c", "s", "http://127.0.0.1:9/", opener=opener)
    assert res["ok"] is False and res["tokens"] is None
    assert "400" in res["error"] and "invalid_grant" in res["error"]


def test_exchange_code_unreachable_endpoint_returns_honest_failure():
    def opener(req, timeout):
        raise urllib.error.URLError(TimeoutError("timed out"))

    res = g.exchange_code("c", "s", "http://127.0.0.1:9/", opener=opener)
    assert res["ok"] is False and res["tokens"] is None and "timed out" in res["error"]


def test_exchange_code_non_json_body_returns_honest_failure():
    res = g.exchange_code("c", "s", "http://127.0.0.1:9/",
                          opener=_opener_returning(b"<html>oops</html>", {}))
    assert res["ok"] is False and res["tokens"] is None and "JSON" in res["error"]


def test_exchange_code_missing_refresh_token_is_a_failure():
    body = json.dumps({"access_token": "at"}).encode()
    res = g.exchange_code("c", "s", "http://127.0.0.1:9/",
                          opener=_opener_returning(body, {}))
    assert res["ok"] is False and "refresh_token" in res["error"]
    assert res["tokens"] == {"access_token": "at"}      # surfaced for diagnosis


# ---------------------------------------------------------------------------
# creds file
# ---------------------------------------------------------------------------

def test_write_creds_writes_0600_json(tmp_path):
    out = tmp_path / "secrets" / "google.json"
    res = g.write_creds("sec", "rt", client_id="cid", scopes=["a"], out_path=out)
    assert res["ok"] is True and res["path"] == str(out)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert json.loads(out.read_text()) == {
        "client_id": "cid", "client_secret": "sec", "refresh_token": "rt", "scopes": ["a"],
    }


def test_write_creds_unwritable_path_is_honest(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")                            # a FILE where a dir must go
    res = g.write_creds("sec", "rt", out_path=blocker / "google.json")
    assert res["ok"] is False and res["error"]
