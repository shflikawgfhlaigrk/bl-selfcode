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


def test_visible_chrome_launches_in_background_never_steals_focus(monkeypatch):
    """Ace's on-screen browser must launch via `open -g` (background) so it never
    yanks the cursor/focus from Michael — the same caret-stealing bug wc_feed fixed.
    A direct foreground Popen of the chrome binary is the regression this guards."""
    spawned = {}

    def fake_spawn(argv):
        spawned["argv"] = list(argv)

    # CDP never answers, so _ensure_visible_chrome must try to spawn — capture the argv.
    monkeypatch.setattr(browser, "_cdp_get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    try:
        browser._ensure_visible_chrome("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                                       wait_s=0.0, spawn_fn=fake_spawn)
    except RuntimeError:
        pass  # expected: CDP never comes up in the test; we only assert HOW it spawned
    argv = spawned.get("argv", [])
    assert argv[:2] == ["open", "-g"], f"must background-launch via `open -g`, got {argv[:3]}"
    assert "-a" in argv and "--args" in argv          # app-launch form, flags after --args
    assert any("remote-debugging-port" in a for a in argv)
