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
import os
import re
import shutil
import subprocess
import time
from collections.abc import Sequence

from utah import failures
from utah.daemon import runtime

log = logging.getLogger("utah.integrations.browser")

CHROME_FLAG = runtime.UTAH_HOME / "secrets" / "chrome.json"

# ── Michael can FULLY SEE Ace's browsing (2026-06-10 directive) ──────────────
#: Every successful render leaves a TRAIL: a real PNG screenshot of the page exactly as
#: Chrome rendered it + a JSONL row (ts, url, chars). Open the folder anytime — that IS
#: Ace's browser history, with pictures. Pruned to the newest TRAIL_KEEP entries.
TRAIL_DIR = runtime.RUN_DIR / "browser_trail"
TRAIL_LOG = TRAIL_DIR / "trail.jsonl"
TRAIL_KEEP = 200
#: LIVE mode: `touch ~/.utah/run/browser.visible` (or UTAH_BROWSER_VISIBLE=1) and the
#: VISIBLE window IS the worker (Michael 2026-06-10: "I need to see live in the Chrome
#: he uses" — not a mirror). Ace keeps ONE persistent on-screen Chrome (own profile,
#: own CDP port — wc_feed owns 9223, this owns 9224), navigates real tabs in it via
#: CDP, and reads the DOM out of the very tabs Michael is watching. Headless
#: --dump-dom remains the default path (flag off) and the honest fallback when the
#: visible browser breaks mid-run.
VISIBLE_FLAG = runtime.RUN_DIR / "browser.visible"
_ACE_PROFILE = runtime.UTAH_HOME / "chrome-ace"
CDP_PORT = int(os.environ.get("UTAH_BROWSER_CDP_PORT", "9224"))
_TAB_KEEP = 4   # visible tabs he can scroll back through; older auto-close
RENDER_TIMEOUT_S = 30
#: Cap how long ``--dump-dom`` waits for "load" before dumping the DOM it has. A page with a
#: PERSISTENT connection (SSE ``EventSource`` / long-poll) — like Ace's own live deck — never
#: reaches network-idle, so without this cap ``--dump-dom`` hangs until the subprocess timeout.
#: With it, Chrome runs the JS and dumps the live rendered DOM after the cap (proven: the live
#: deck renders in ~2s, ~86k chars). ``--virtual-time-budget`` does NOT work here (the endless
#: SSE event stream consumes the budget) — ``--timeout`` is the correct primitive.
LOAD_SETTLE_MS = 5000

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
    try:
        cfg = json.loads(CHROME_FLAG.read_text())
        named = cfg.get("binary") if isinstance(cfg, dict) else None
        if named and (shutil.which(named) or os.path.exists(named)):
            return named
    except (OSError, ValueError):  # missing/garbled override — fall through to auto-detect
        pass
    for cand in _CHROME_CANDIDATES:
        hit = cand if cand.startswith("/") and os.path.exists(cand) else shutil.which(cand)
        if hit:
            return hit
    return None


def available() -> bool:
    """True when a real Chrome is resolvable — the browser works out of the box."""
    return chrome_binary() is not None


def _chrome_render(url: str, timeout: int = RENDER_TIMEOUT_S) -> str:
    """Render *url* with headless Chrome and return the post-JS DOM. Raises on failure.

    Uses ``--dump-dom`` (runs the page's JS, dumps the rendered DOM) bounded by ``--timeout``
    so a page with a PERSISTENT connection (an SSE ``EventSource`` / long-poll) — like Ace's
    OWN live deck — dumps its live, JS-rendered DOM after the settle cap instead of hanging
    forever. The cap is held a few seconds under the subprocess ``timeout`` so Chrome dumps
    before we give up. Static/quiescent pages still dump immediately on load."""
    chrome = chrome_binary()
    if not chrome:
        raise RuntimeError("no Chrome binary found")
    settle_ms = max(1000, min(LOAD_SETTLE_MS, timeout * 1000 - 3000))
    proc = subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--dump-dom",
         f"--timeout={settle_ms}", url],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError((proc.stderr or "chrome produced no DOM").strip()[-300:])
    return proc.stdout


def visible() -> bool:
    """True when Michael flipped live-watch mode on (flag file or env)."""
    return VISIBLE_FLAG.exists() or os.environ.get("UTAH_BROWSER_VISIBLE") == "1"


def _cdp_get(path: str, *, method: str = "GET", timeout: float = 4.0) -> object:
    import urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:{CDP_PORT}{path}", method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "null")


def _chrome_app_bundle(chrome: str) -> str:
    """The ``.app`` bundle for *chrome* (a binary path) so we can ``open -g -a`` it in
    the BACKGROUND. Falls back to the well-known app name when the path isn't a bundle."""
    marker = ".app/"
    i = chrome.find(marker)
    if i != -1:
        return chrome[: i + len(".app")]
    return "Google Chrome"


def _bg_spawn(argv: Sequence[str]) -> None:
    subprocess.Popen(list(argv), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _ensure_visible_chrome(chrome: str, *, wait_s: float = 15.0, spawn_fn=None) -> None:
    """Ace's one persistent ON-SCREEN browser: spawn if the CDP port isn't answering,
    then wait until it is. Own profile + own port — never touches Michael's browsers.

    Launched via ``open -g`` (BACKGROUND) so it NEVER steals focus or yanks the cursor
    away from Michael — the same caret-stealing bug wc_feed fixed (a direct foreground
    Popen of the chrome binary activates the app on every spawn)."""
    try:
        _cdp_get("/json/version")
        return
    except Exception:  # noqa: BLE001 — not up yet
        pass
    spawn = spawn_fn or _bg_spawn
    spawn(
        ["open", "-g", "-n", "-a", _chrome_app_bundle(chrome), "--args",
         f"--remote-debugging-port={CDP_PORT}", "--remote-allow-origins=*",
         f"--user-data-dir={_ACE_PROFILE}", "--no-first-run", "--no-default-browser-check",
         "--window-size=980,740", "--window-position=60,60", "about:blank"])
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        try:
            _cdp_get("/json/version")
            return
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    raise RuntimeError(f"visible Chrome did not open CDP port {CDP_PORT} in {wait_s:.0f}s")


def _prune_tabs() -> None:
    """Keep the newest _TAB_KEEP page tabs in Ace's window so Michael can scroll back;
    close the rest. Best-effort."""
    try:
        pages = [p for p in (_cdp_get("/json") or [])
                 if p.get("type") == "page" and not (p.get("url") or "").startswith("chrome")]
        for p in pages[_TAB_KEEP:]:
            _cdp_get(f"/json/close/{p['id']}")
    except Exception:  # noqa: BLE001
        pass


def _visible_render(url: str, timeout: int = RENDER_TIMEOUT_S) -> str:
    """Render *url* IN the on-screen window and return its DOM — the watched browser is
    the worker. CDP: open a real tab, poll readyState until 'complete' (or the settle
    cap — SSE pages load fine, they just never go idle), then evaluate outerHTML."""
    import asyncio

    chrome = chrome_binary()
    if not chrome:
        raise RuntimeError("no Chrome binary found")
    _ensure_visible_chrome(chrome)
    import urllib.parse
    page = _cdp_get("/json/new?" + urllib.parse.quote(url, safe=":/?&=%"), method="PUT")
    ws_url = (page or {}).get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError("CDP gave no webSocketDebuggerUrl for the new tab")

    async def _dom() -> str:
        import websockets
        async with websockets.connect(ws_url, max_size=None) as ws:
            msg_id = 0

            async def call(method: str, params: dict | None = None) -> dict:
                nonlocal msg_id
                msg_id += 1
                await ws.send(json.dumps({"id": msg_id, "method": method,
                                          "params": params or {}}))
                while True:
                    resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    if resp.get("id") == msg_id:
                        return resp.get("result") or {}

            settle = max(2.0, min(LOAD_SETTLE_MS / 1000.0, timeout - 5.0))
            deadline = time.monotonic() + settle
            while time.monotonic() < deadline:
                state = await call("Runtime.evaluate",
                                   {"expression": "document.readyState",
                                    "returnByValue": True})
                if ((state.get("result") or {}).get("value")) == "complete":
                    break
                await asyncio.sleep(0.5)
            out = await call("Runtime.evaluate",
                             {"expression": "document.documentElement.outerHTML",
                              "returnByValue": True})
            html = (out.get("result") or {}).get("value") or ""
            if not html:
                raise RuntimeError("visible tab returned an empty DOM")
            return html

    html = asyncio.run(asyncio.wait_for(_dom(), timeout=timeout))
    _prune_tabs()
    return html


def _slug(url: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", url.lower())[:60].strip("-") or "page"


def _prune_trail(keep: int | None = None) -> None:
    """Newest *keep* JSONL rows + matching PNGs survive; everything older goes.
    ``keep`` resolves at CALL time (not def time) so tests/config can retune it."""
    try:
        keep = keep if keep is not None else TRAIL_KEEP
        rows = TRAIL_LOG.read_text(encoding="utf-8").splitlines()[-keep:]
        TRAIL_LOG.write_text("\n".join(rows) + "\n", encoding="utf-8")
        keep_pngs = {json.loads(r).get("shot") for r in rows if r.strip()}
        for png in TRAIL_DIR.glob("*.png"):
            if str(png) not in keep_pngs:
                png.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 — pruning is housekeeping, never load-bearing
        pass


def _leave_trail(url: str, chars: int, *, shot_fn=None, open_fn=None) -> None:
    """Best-effort, NEVER raises, never blocks the render result: write the screenshot
    + JSONL row, and in visible mode also open the page in Ace's on-screen window."""
    try:
        TRAIL_DIR.mkdir(parents=True, exist_ok=True)
        shot = TRAIL_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{_slug(url)}.png"
        chrome = chrome_binary()
        if chrome:
            (shot_fn or (lambda: subprocess.run(
                [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--window-size=1280,900", f"--screenshot={shot}",
                 f"--timeout={LOAD_SETTLE_MS}", url],
                capture_output=True, timeout=20)))()
        with TRAIL_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "url": url, "chars": chars,
                                "shot": str(shot) if shot.exists() else ""}) + "\n")
        _prune_trail()
    except Exception as exc:  # noqa: BLE001
        log.debug("browser trail skipped: %s", exc)


def trail(limit: int = 20) -> list[dict]:
    """Newest-first browse history (what Ace read, when, and the screenshot path) —
    the console's ``/browse`` reads this. Honest-empty when he hasn't browsed."""
    try:
        rows = [json.loads(r) for r in
                TRAIL_LOG.read_text(encoding="utf-8").splitlines() if r.strip()]
        return rows[-limit:][::-1]
    except Exception:  # noqa: BLE001 — no trail yet
        return []


def render(url: str, *, render_fn=None, timeout: int = RENDER_TIMEOUT_S) -> dict:
    """Fetch a JS-rendered page via headless Chrome. With no Chrome it documents the gate and
    returns ``rendered=False`` (never fabricates). ``render_fn`` is injectable for tests;
    ``timeout`` bounds the subprocess (a page with a persistent connection won't settle, so a
    short timeout lets a caller fail fast and fall back instead of blocking the default 30s)."""
    if render_fn is not None:
        fn = render_fn
    elif available() and visible():
        # LIVE: the on-screen window IS the worker. If it breaks (window closed mid-
        # run, port hijacked), record it and fall back headless — data keeps flowing.
        def fn(u):
            try:
                return _visible_render(u, timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                failures.record("browser", "visible_render_failed",
                                f"{u[:50]}: {exc} — fell back to headless")
                return _chrome_render(u, timeout=timeout)
    elif available():
        def fn(u):
            return _chrome_render(u, timeout=timeout)
    else:
        fn = None
    if fn is None:
        failures.record("browser", "gated",
                        f"render {url[:50]} gated: no headless Chrome found; "
                        "static pages use researcher.fetch")
        return {"rendered": False, "gated": True, "url": url}
    try:
        html = fn(url)
        if render_fn is None:           # real browse → leave the visible trail
            _leave_trail(url, len(html))
        return {"rendered": True, "gated": False, "url": url, "html": html, "chars": len(html)}
    except Exception as exc:  # noqa: BLE001
        failures.record("browser", "render_failed", f"{url[:50]}: {exc}")
        return {"rendered": False, "gated": False, "error": str(exc), "url": url}


__all__ = ["render", "available", "chrome_binary", "CHROME_FLAG",
           "trail", "visible", "VISIBLE_FLAG", "TRAIL_DIR", "CDP_PORT"]
