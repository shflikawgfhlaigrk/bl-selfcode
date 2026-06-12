"""ops/drill.py — the probe layer and the failure-recording boundary.

The deck probes must interpret REAL HTTP JSON honestly (a deck that answers but
has no live data is NOT recovered), the launchctl kick must stay bounded and
injectable, and missed SLOs must land in the failures ledger through a recorder
seam — a broken ledger degrades to a logged warning, never a crashed drill.
Every HTTP test runs against a real local server, never a mocked drill.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import stat
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

DRILL_PY = pathlib.Path(__file__).resolve().parents[1] / "ops" / "drill.py"


def _load(env: dict | None = None):
    """Load a fresh ops/drill.py module with *env* applied for the import."""
    saved = {}
    env = env or {}
    for key, val in env.items():
        saved[key] = os.environ.get(key)
        os.environ[key] = val
    try:
        spec = importlib.util.spec_from_file_location("ops_drill_under_test", DRILL_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for key, val in saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val


@pytest.fixture()
def deck():
    """A real local HTTP deck: tests set payloads[path] = dict."""
    payloads: dict[str, dict] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — http.server API
            body = json.dumps(payloads.get(self.path, {})).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence test output
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield payloads, f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# probes against a real deck
# ---------------------------------------------------------------------------

def test_deck_url_is_env_overridable(deck):
    _payloads, base = deck
    mod = _load({"UTAH_DECK_URL": base})
    assert mod.DECK == base


def test_deck_answers_requires_governor(deck):
    payloads, base = deck
    mod = _load({"UTAH_DECK_URL": base})
    payloads["/status"] = {"governor": {"load": 1.2}}
    ok, _ = mod._deck_answers()
    assert ok is True
    payloads["/status"] = {}
    ok, _ = mod._deck_answers()
    assert ok is False


def test_deck_data_live_requires_live_health_and_leads(deck):
    payloads, base = deck
    mod = _load({"UTAH_DECK_URL": base})
    payloads["/state"] = {"health": "live", "ledger": {"leads": 4666}}
    assert mod._deck_data_live()[0] is True
    payloads["/state"] = {"health": "degraded", "ledger": {"leads": 4666}}
    assert mod._deck_data_live()[0] is False, "degraded deck must not count as recovered"
    payloads["/state"] = {"health": "live", "ledger": {"leads": 0}}
    assert mod._deck_data_live()[0] is False, "zero leads must not count as recovered"


def test_voice_up_rejects_down_and_empty(deck):
    payloads, base = deck
    mod = _load({"UTAH_DECK_URL": base})
    for bad in ({"status": "down"}, {"status": ""}, {}):
        payloads["/panel/voice"] = bad
        assert mod._voice_up()[0] is False
    payloads["/panel/voice"] = {"status": "listening"}
    assert mod._voice_up()[0] is True


def test_http_json_is_bounded():
    """A probe against a non-answering port must fail fast, not hang the drill."""
    mod = _load()
    t0 = time.monotonic()
    with pytest.raises(Exception):
        mod._http_json("http://127.0.0.1:1/status", timeout=0.5)
    assert time.monotonic() - t0 < 5


# ---------------------------------------------------------------------------
# _kick — bounded, injectable launchctl
# ---------------------------------------------------------------------------

def _write_exe(path: pathlib.Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_kick_calls_launchctl_kickstart(tmp_path):
    calls = tmp_path / "calls.log"
    fake = tmp_path / "launchctl"
    _write_exe(fake, f'echo "$@" >> "{calls}"\nexit 0\n')
    mod = _load({"UTAH_LAUNCHCTL": str(fake)})
    mod._kick("com.utah.supervisor")
    logged = calls.read_text()
    assert "kickstart -k" in logged and "com.utah.supervisor" in logged


def test_kick_raises_on_launchctl_failure(tmp_path):
    fake = tmp_path / "launchctl"
    _write_exe(fake, "exit 1\n")
    mod = _load({"UTAH_LAUNCHCTL": str(fake)})
    with pytest.raises(subprocess.CalledProcessError):
        mod._kick("com.utah.supervisor")


def test_kick_is_bounded_against_a_wedged_launchctl(tmp_path):
    fake = tmp_path / "launchctl"
    _write_exe(fake, "exec sleep 30\n")
    mod = _load({"UTAH_LAUNCHCTL": str(fake), "UTAH_KICK_TIMEOUT": "1"})
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        mod._kick("com.utah.supervisor")
    assert time.monotonic() - t0 < 10


# ---------------------------------------------------------------------------
# run() — failure recording through the injectable seam
# ---------------------------------------------------------------------------

def test_run_records_each_missed_slo(tmp_path):
    mod = _load()
    recorded = []
    drills = [
        ("svc.a", [("never", lambda: (False, "dead"), 0.05)]),
        ("svc.b", [("up", lambda: (True, "up"), 1)]),
    ]
    out = mod.run(drills, kick=lambda label: None,
                  record=lambda *a: recorded.append(a))
    assert out["ok"] is False
    assert recorded == [("drill", "restart_slo_missed", "svc.a/never")]


def test_run_survives_a_broken_recorder():
    mod = _load()

    def broken(*_a):
        raise RuntimeError("ledger down")

    drills = [("svc.a", [("never", lambda: (False, "dead"), 0.05)])]
    out = mod.run(drills, kick=lambda label: None, record=broken)
    assert out["ok"] is False and out["failed"] == ["svc.a/never"]


def test_run_all_green_records_nothing():
    mod = _load()
    recorded = []
    drills = [("svc.a", [("up", lambda: (True, "up"), 1)])]
    out = mod.run(drills, kick=lambda label: None,
                  record=lambda *a: recorded.append(a))
    assert out["ok"] is True and out["failed"] == [] and recorded == []
