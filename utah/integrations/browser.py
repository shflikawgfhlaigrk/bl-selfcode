"""Browser capability — Ace's chrome-ace browser transitions here (NOT an agent): fetch a
JS-rendered page. The real driver is headless Chrome ``--dump-dom`` (executes JS, dumps the
rendered DOM) — no extra dependency, fail-loud. For STATIC pages the researcher's urllib
fetch already works; this is for JS-rendered content. Activates automatically when a Chrome
binary is found (or one is named in ``~/.utah/secrets/chrome.json``); with no Chrome it
documents the gate and returns ``rendered=False`` — never fabricates a page.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess

from utah import failures
from utah.daemon import runtime

log = logging.getLogger("utah.integrations.browser")

CHROME_FLAG = runtime.UTAH_HOME / "secrets" / "chrome.json"
RENDER_TIMEOUT_S = 30
VIRTUAL_TIME_BUDGET_MS = 6000  # let JS settle before the DOM is dumped

#: Common Chrome/Chromium install locations (macOS + linux). chrome.json {"binary": "..."}
#: overrides; otherwise the first that exists wins.
_CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
)


def chrome_binary() -> str | None:
    """Resolve a Chrome binary: chrome.json ``binary`` first, then known paths/PATH.
    Returns the executable path, or ``None`` if no Chrome is installed."""
    if CHROME_FLAG.exists():
        try:
            cfg = json.loads(CHROME_FLAG.read_text())
            b = cfg.get("binary")
            if b and (shutil.which(b) or __import__("os").path.exists(b)):
                return b
        except Exception:  # noqa: BLE001 — fall through to auto-detect
            pass
    for cand in _CHROME_CANDIDATES:
        hit = cand if cand.startswith("/") and __import__("os").path.exists(cand) else shutil.which(cand)
        if hit:
            return hit
    return None


def available() -> bool:
    """True when a real Chrome is resolvable — the browser works out of the box."""
    return chrome_binary() is not None


def _chrome_render(url: str) -> str:
    """Render *url* with headless Chrome and return the post-JS DOM. Raises on failure."""
    chrome = chrome_binary()
    if not chrome:
        raise RuntimeError("no Chrome binary found")
    proc = subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--dump-dom",
         f"--virtual-time-budget={VIRTUAL_TIME_BUDGET_MS}", url],
        capture_output=True, text=True, timeout=RENDER_TIMEOUT_S,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError((proc.stderr or "chrome produced no DOM").strip()[-300:])
    return proc.stdout


def render(url: str, *, render_fn=None) -> dict:
    """Fetch a JS-rendered page via headless Chrome. With no Chrome it documents the gate and
    returns ``rendered=False`` (never fabricates). ``render_fn`` is injectable for tests."""
    fn = render_fn or (_chrome_render if available() else None)
    if fn is None:
        failures.record("browser", "gated",
                        f"render {url[:50]} gated: no headless Chrome found; "
                        "static pages use researcher.fetch")
        return {"rendered": False, "gated": True, "url": url}
    try:
        html = fn(url)
        return {"rendered": True, "gated": False, "url": url, "html": html, "chars": len(html)}
    except Exception as exc:  # noqa: BLE001
        failures.record("browser", "render_failed", f"{url[:50]}: {exc}")
        return {"rendered": False, "gated": False, "error": str(exc), "url": url}


__all__ = ["render", "available", "chrome_binary", "CHROME_FLAG"]
