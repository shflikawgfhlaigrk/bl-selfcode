"""Browser driver hardening — binary resolution, bounded Chrome subprocess, settle-cap
clamping, visible-mode fallback, tab pruning. Zero real Chrome, zero network."""
from __future__ import annotations

import json

import pytest

from utah import failures
from utah.integrations import browser
from tests.fakes import FakeFailureStore


# --- chrome_binary resolution --------------------------------------------------
def test_chrome_json_override_wins(tmp_path, monkeypatch):
    binpath = tmp_path / "mychrome"
    binpath.write_text("#!/bin/sh\n")
    binpath.chmod(0o755)
    flag = tmp_path / "chrome.json"
    flag.write_text(json.dumps({"binary": str(binpath)}))
    monkeypatch.setattr(browser, "CHROME_FLAG", flag)
    assert browser.chrome_binary() == str(binpath)
    assert browser.available() is True


def test_garbled_or_dead_override_falls_through_to_autodetect(tmp_path, monkeypatch):
    flag = tmp_path / "chrome.json"
    monkeypatch.setattr(browser, "CHROME_FLAG", flag)
    monkeypatch.setattr(browser, "_CHROME_CANDIDATES", ())   # nothing to detect
    flag.write_text("{garbled")
    assert browser.chrome_binary() is None
    flag.write_text(json.dumps({"binary": "/definitely/not/installed/chrome"}))
    assert browser.chrome_binary() is None
    assert browser.available() is False


# --- the headless driver is bounded ---------------------------------------------
def _fake_run(seen, *, returncode=0, stdout="<html>x</html>", stderr=""):
    def run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw

        class P:
            pass
        P.returncode = returncode
        P.stdout = stdout
        P.stderr = stderr
        return P()
    return run


def test_chrome_render_dumps_dom_with_bounds(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(browser.subprocess, "run", _fake_run(seen))
    monkeypatch.setattr(browser, "chrome_binary", lambda: "/fake/chrome")
    html = browser._chrome_render("https://x.test", timeout=30)
    assert html == "<html>x</html>"
    assert seen["kw"]["timeout"] == 30                      # subprocess is bounded…
    assert "--dump-dom" in seen["cmd"] and seen["cmd"][-1] == "https://x.test"
    assert any(a.startswith("--timeout=") for a in seen["cmd"])   # …and so is Chrome


def test_settle_cap_clamps_under_the_subprocess_timeout(monkeypatch):
    """A 5s caller budget must not hand Chrome the default 5s settle — Chrome would
    still be loading when the subprocess axe falls and every render would die."""
    seen: dict = {}
    monkeypatch.setattr(browser.subprocess, "run", _fake_run(seen))
    monkeypatch.setattr(browser, "chrome_binary", lambda: "/fake/chrome")
    browser._chrome_render("https://x.test", timeout=5)
    settle = next(int(a.split("=", 1)[1]) for a in seen["cmd"] if a.startswith("--timeout="))
    assert 1000 <= settle <= 2000                           # clamped, never the full 5000


def test_chrome_render_raises_stderr_on_failure(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(browser.subprocess, "run",
                        _fake_run(seen, returncode=1, stdout="", stderr="chrome crashed hard"))
    monkeypatch.setattr(browser, "chrome_binary", lambda: "/fake/chrome")
    with pytest.raises(RuntimeError, match="chrome crashed hard"):
        browser._chrome_render("https://x.test")


def test_chrome_render_without_chrome_raises(monkeypatch):
    monkeypatch.setattr(browser, "chrome_binary", lambda: None)
    with pytest.raises(RuntimeError, match="no Chrome"):
        browser._chrome_render("https://x.test")


def test_empty_dom_is_a_failure_not_a_blank_page(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(browser.subprocess, "run", _fake_run(seen, stdout=""))
    monkeypatch.setattr(browser, "chrome_binary", lambda: "/fake/chrome")
    with pytest.raises(RuntimeError):
        browser._chrome_render("https://x.test")


# --- render orchestration ---------------------------------------------------------
def _quiet_trail(monkeypatch, tmp_path):
    monkeypatch.setattr(browser, "TRAIL_DIR", tmp_path)
    monkeypatch.setattr(browser, "TRAIL_LOG", tmp_path / "trail.jsonl")
    monkeypatch.setattr(browser, "chrome_binary", lambda: None)   # no screenshot subprocess


def test_visible_mode_falls_back_to_headless_and_records(monkeypatch, tmp_path):
    """Michael's on-screen window dying mid-run must DEGRADE (headless render), not
    drop the page — and the fall-back is documented, never silent."""
    store = FakeFailureStore(); failures.set_store(store)
    _quiet_trail(monkeypatch, tmp_path)
    monkeypatch.setattr(browser, "available", lambda: True)
    monkeypatch.setattr(browser, "visible", lambda: True)

    def window_gone(url, timeout=browser.RENDER_TIMEOUT_S):
        raise RuntimeError("window gone")

    monkeypatch.setattr(browser, "_visible_render", window_gone)
    monkeypatch.setattr(browser, "_chrome_render",
                        lambda url, timeout=browser.RENDER_TIMEOUT_S: "<html>fallback</html>")
    r = browser.render("https://x.test")
    assert r["rendered"] is True and r["html"] == "<html>fallback</html>"
    assert any("visible_render_failed" in row[2] for row in store.rows)
    assert browser.trail() and browser.trail()[0]["url"] == "https://x.test"


def test_render_forwards_caller_timeout_to_the_driver(monkeypatch, tmp_path):
    failures.set_store(FakeFailureStore())
    _quiet_trail(monkeypatch, tmp_path)
    monkeypatch.setattr(browser, "available", lambda: True)
    monkeypatch.setattr(browser, "visible", lambda: False)
    seen: dict = {}

    def fake_render(url, timeout=browser.RENDER_TIMEOUT_S):
        seen["timeout"] = timeout
        return "<x/>"

    monkeypatch.setattr(browser, "_chrome_render", fake_render)
    r = browser.render("https://x.test", timeout=7)
    assert r["rendered"] is True and seen["timeout"] == 7


# --- visible-window housekeeping ----------------------------------------------------
def test_prune_tabs_keeps_newest_pages_and_ignores_chrome_urls(monkeypatch):
    closed: list = []
    pages = [{"type": "page", "url": f"https://x/{i}", "id": f"id{i}"} for i in range(6)]
    pages.append({"type": "page", "url": "chrome://newtab", "id": "internal"})

    def fake_cdp(path, *, method="GET", timeout=4.0):
        if path == "/json":
            return list(pages)
        closed.append(path)
        return {}

    monkeypatch.setattr(browser, "_cdp_get", fake_cdp)
    browser._prune_tabs()
    assert closed == ["/json/close/id4", "/json/close/id5"]   # newest 4 kept, chrome:// untouched
