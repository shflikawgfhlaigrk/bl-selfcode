"""Browser capability — Ace's browser transitions here (NOT an agent): fetch a JS-rendered
page. GATED on a headless Chrome (flag ``~/.utah/secrets/chrome.json``). For STATIC pages the
researcher's urllib fetch already works; this is for JS-rendered content and activates when
Chrome is wired. With no Chrome it documents the gate and returns rendered=False.
"""
from __future__ import annotations

import logging

from utah import failures
from utah.daemon import runtime

log = logging.getLogger("utah.integrations.browser")

CHROME_FLAG = runtime.UTAH_HOME / "secrets" / "chrome.json"


def available() -> bool:
    return CHROME_FLAG.exists()


def render(url: str, *, render_fn=None) -> dict:
    """Fetch a JS-rendered page. Gated on Chrome; documents the gate with none. Never raises."""
    if render_fn is None and not available():
        failures.record("browser", "gated",
                        f"render {url[:50]} gated: no headless Chrome ({CHROME_FLAG}); "
                        "static pages use researcher.fetch")
        return {"rendered": False, "gated": True, "url": url}
    try:
        return {"rendered": True, "gated": False, "url": url, "html": (render_fn or _no_chrome)(url)}
    except Exception as exc:  # noqa: BLE001
        failures.record("browser", "render_failed", f"{url[:50]}: {exc}")
        return {"rendered": False, "gated": False, "error": str(exc)}


def _no_chrome(_):  # pragma: no cover
    raise RuntimeError(f"headless Chrome not configured ({CHROME_FLAG})")


__all__ = ["render", "available", "CHROME_FLAG"]
