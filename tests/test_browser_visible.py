"""Live-watch browser mode: the visible window IS the worker; trail records every page."""
from __future__ import annotations

import json

from utah.integrations import browser


def test_slug_and_trail_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "TRAIL_DIR", tmp_path)
    monkeypatch.setattr(browser, "TRAIL_LOG", tmp_path / "trail.jsonl")
    browser._leave_trail("https://example.com/a?b=1", 1234, shot_fn=lambda: None)
    rows = browser.trail()
    assert len(rows) == 1 and rows[0]["url"] == "https://example.com/a?b=1"
    assert rows[0]["chars"] == 1234


def test_trail_prunes_to_keep(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "TRAIL_DIR", tmp_path)
    monkeypatch.setattr(browser, "TRAIL_LOG", tmp_path / "trail.jsonl")
    monkeypatch.setattr(browser, "TRAIL_KEEP", 5)
    for i in range(9):
        browser._leave_trail(f"https://x.com/{i}", i, shot_fn=lambda: None)
    rows = browser.trail(limit=50)
    assert len(rows) == 5 and rows[0]["url"].endswith("/8")   # newest kept, oldest gone


def test_visible_flag_detection(tmp_path, monkeypatch):
    flag = tmp_path / "browser.visible"
    monkeypatch.setattr(browser, "VISIBLE_FLAG", flag)
    monkeypatch.delenv("UTAH_BROWSER_VISIBLE", raising=False)
    assert browser.visible() is False
    flag.touch()
    assert browser.visible() is True


def test_render_with_injected_fn_untouched_by_modes(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "TRAIL_DIR", tmp_path)
    monkeypatch.setattr(browser, "TRAIL_LOG", tmp_path / "trail.jsonl")
    r = browser.render("https://x.com", render_fn=lambda u: "<html>ok</html>")
    assert r["rendered"] and r["chars"] == 15
    assert browser.trail() == []        # injected test renders never pollute the trail
