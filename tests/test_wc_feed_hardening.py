"""wc_feed hardening — junk-proof CDP frame handling (one malformed frame must never
drop the persistent hook), env tunable parsing that can't crash the import, CDP
probe/tab-reopen honesty when chrome is down, pure-helper edges (empty/single-bucket
bars, max_bars trim), and the kill-escalation path."""
from __future__ import annotations

import io
import json as _json
import signal

from utah.integrations import wc_feed

REAL = ('{"cmd":"feed","data":{"type":"candle","c":"CM.MNQM6","candle":'
        '{"co":29374.0,"cM":29380.0,"cm":29370.0,"cc":29376.5,"cepoch":1781045182,"type":"rt"}}}')


def _frame(payload: str) -> str:
    return _json.dumps({"method": "Network.webSocketFrameReceived",
                        "params": {"response": {"payloadData": payload}}})


# ── _frame_candle: the hook's per-frame boundary ──────────────────────────────

def test_frame_candle_parses_a_real_cdp_frame():
    c = wc_feed._frame_candle(_frame(REAL))
    assert c and c["symbol"] == "CM.MNQM6" and c["close"] == 29376.5


def test_frame_candle_never_raises_on_junk():
    """A single malformed CDP frame used to kill the WHOLE persistent hook (the
    json.loads ran bare inside the stream loop) — every variant must be a quiet None."""
    assert wc_feed._frame_candle("not json at all") is None
    assert wc_feed._frame_candle("[]") is None                       # non-dict
    assert wc_feed._frame_candle('{"method":"Network.loadingFinished"}') is None
    assert wc_feed._frame_candle('{"method":"Network.webSocketFrameReceived"}') is None
    assert wc_feed._frame_candle(
        '{"method":"Network.webSocketFrameReceived","params":{}}') is None
    assert wc_feed._frame_candle(
        '{"method":"Network.webSocketFrameReceived","params":{"response":{"payloadData":7}}}'
    ) is None
    assert wc_feed._frame_candle(_frame('{"cmd":"keepalive"}')) is None


# ── env tunables can't crash the import ───────────────────────────────────────

def test_env_int_parses_and_falls_back(monkeypatch):
    monkeypatch.setenv("UTAH_WC_TEST_INT", "42")
    assert wc_feed._env_int("UTAH_WC_TEST_INT", 7) == 42
    monkeypatch.setenv("UTAH_WC_TEST_INT", "garbage")
    assert wc_feed._env_int("UTAH_WC_TEST_INT", 7) == 7
    monkeypatch.delenv("UTAH_WC_TEST_INT")
    assert wc_feed._env_int("UTAH_WC_TEST_INT", 7) == 7


# ── CDP probe honesty when chrome is down ─────────────────────────────────────

def test_cdp_pages_none_when_both_loopbacks_refuse(monkeypatch):
    def refused(url, timeout=4):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", refused)
    assert wc_feed._cdp_pages() is None
    assert wc_feed._cdp_reachable() is False
    assert wc_feed.feed_available() is False         # honest gate, engines stay dormant


def test_wc_page_skips_login_wall_and_pages_without_ws(monkeypatch):
    login = {"type": "page", "url": f"https://{wc_feed.WC_HOST}/login",
             "webSocketDebuggerUrl": "ws://x"}
    no_ws = {"type": "page", "url": f"https://{wc_feed.WC_HOST}/"}
    other = {"type": "page", "url": "https://example.com/",
             "webSocketDebuggerUrl": "ws://y"}
    good = {"type": "page", "url": f"https://{wc_feed.WC_HOST}/dash",
            "webSocketDebuggerUrl": "ws://good"}
    monkeypatch.setattr(wc_feed, "_cdp_pages", lambda: [login, no_ws, other, good])
    assert wc_feed._wc_page() == good
    monkeypatch.setattr(wc_feed, "_cdp_pages", lambda: [login, no_ws, other])
    assert wc_feed._wc_page() is None
    assert wc_feed._wc_any_page() == login           # any-WC check still sees the wall


def test_collect_ticks_empty_when_no_page(monkeypatch):
    monkeypatch.setattr(wc_feed, "_wc_page", lambda: None)
    assert wc_feed.collect_ticks(seconds=0.01) == {}


def test_stream_returns_immediately_when_no_page(monkeypatch):
    monkeypatch.setattr(wc_feed, "_wc_page", lambda: None)
    assert wc_feed.stream(ledger=object(), interval=0.01) is None


# ── tab reopen via CDP ────────────────────────────────────────────────────────

def test_open_wc_tab_puts_json_new_on_cdp(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=4):
        seen["url"] = req.get_full_url()
        seen["method"] = req.get_method()
        return io.StringIO("{}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert wc_feed._open_wc_tab() is True
    assert seen["method"] == "PUT" and "/json/new" in seen["url"]
    assert wc_feed.WC_HOST in seen["url"]


def test_open_wc_tab_false_when_cdp_unreachable(monkeypatch):
    def refused(req, timeout=4):
        raise OSError("refused")

    monkeypatch.setattr("urllib.request.urlopen", refused)
    assert wc_feed._open_wc_tab() is False


# ── pure helper edges ─────────────────────────────────────────────────────────

def test_bar_helpers_empty_and_single_bucket_yield_nothing():
    assert wc_feed.closed_bars([], bar_seconds=10) == []
    assert wc_feed.ohlc_bars([], bar_seconds=10) == []
    # one bucket only = still forming -> no closed bar, never a premature close
    assert wc_feed.closed_bars([(100, 1.0), (105, 2.0)], bar_seconds=10) == []
    assert wc_feed.ohlc_bars([(100, 1.0)], bar_seconds=10) == []
    # None values are skipped, not crashed on
    assert wc_feed.closed_bars([(None, 1.0), (100, None)], bar_seconds=10) == []


def test_ingest_trims_history_to_max_bars():
    state = {}
    ticks = [(i, float(i)) for i in range(50)]       # 49 closed 1s bars + 1 forming
    closes = wc_feed.ingest(state, "X", ticks, bar_seconds=1, max_bars=10)
    assert len(closes) == 10 and closes[-1] == 48.0  # newest kept, history bounded


# ── pid liveness + kill escalation ────────────────────────────────────────────

def test_pid_alive_taxonomy(monkeypatch):
    def kill(pid, sig):
        if pid == 1:
            raise ProcessLookupError
        if pid == 2:
            raise PermissionError                    # EPERM = something IS there
        return None

    monkeypatch.setattr(wc_feed.os, "kill", kill)
    assert wc_feed._pid_alive(1) is False
    assert wc_feed._pid_alive(2) is True
    assert wc_feed._pid_alive(3) is True


def test_kill_wc_chrome_terms_then_skips_kill_when_dead(monkeypatch):
    sent = []
    monkeypatch.setattr(wc_feed.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    monkeypatch.setattr(wc_feed, "_pid_alive", lambda pid: False)    # died on TERM
    wc_feed._kill_wc_chrome([11, 22])
    assert (11, signal.SIGTERM) in sent and (22, signal.SIGTERM) in sent
    assert all(sig != signal.SIGKILL for _, sig in sent)


def test_kill_wc_chrome_escalates_to_sigkill_when_term_ignored(monkeypatch):
    sent = []
    monkeypatch.setattr(wc_feed.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    monkeypatch.setattr(wc_feed, "_pid_alive", lambda pid: True)     # TERM ignored
    clock = {"t": 0.0}

    def mono():
        clock["t"] += 5.0
        return clock["t"]

    monkeypatch.setattr("time.monotonic", mono)
    monkeypatch.setattr("time.sleep", lambda s: None)
    wc_feed._kill_wc_chrome([33])
    assert (33, signal.SIGTERM) in sent and (33, signal.SIGKILL) in sent
