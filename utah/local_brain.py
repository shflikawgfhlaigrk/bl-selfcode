"""On-device Foundation Models brain — zero API cost alternative to Claude.

Gate: set UTAH_USE_LOCAL_BRAIN=1 to route eligible tasks here.
Falls back to claude automatically if the model is unavailable or errors.

Eligible tasks (Foundation Models handles well):
  - extract_facts(): pull structured data from short exchanges
  - caption(): generate social post captions from signal/trade data
  - summarize(): short-form summaries of text

NOT routed here (needs Claude):
  - selfcode, operator reasoning, complex multi-step tasks
  - anything that requires world knowledge or code generation

The runner is injectable (set_runner) for tests — same contract as brain.py.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger("utah.local_brain")

_FM_BIN = Path(os.environ.get("UTAH_FM_BIN", str(Path.home() / ".utah/bin/fm_ask")))
_DEFAULT_TIMEOUT = int(os.environ.get("UTAH_LOCAL_BRAIN_TIMEOUT", "15"))

_runner = None
_runner_lock = __import__("threading").Lock()


def enabled() -> bool:
    return os.environ.get("UTAH_USE_LOCAL_BRAIN", "").strip() == "1"


def available() -> bool:
    return _FM_BIN.exists() and _FM_BIN.is_file()


def set_runner(fn):
    global _runner
    with _runner_lock:
        _runner = fn


def _default_runner(prompt: str, system: str | None = None, timeout: int = _DEFAULT_TIMEOUT) -> str:
    cmd = [str(_FM_BIN)]
    if system:
        cmd += ["--system", system]
    result = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"fm_ask exit {result.returncode}: {result.stderr.strip()}")
    return result.stdout.strip()


def ask(prompt: str, system: str | None = None, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """Run a prompt through Foundation Models. Raises RuntimeError on failure."""
    with _runner_lock:
        run = _runner
    if run is not None:
        return run(prompt, system, timeout)
    return _default_runner(prompt, system, timeout)


def ask_or_none(prompt: str, system: str | None = None) -> str | None:
    """Ask Foundation Models; return None on any failure (caller falls back to Claude)."""
    if not enabled() or not available():
        return None
    try:
        return ask(prompt, system)
    except Exception as exc:
        log.debug("local_brain unavailable, will fall back to Claude: %s", exc)
        return None


# ── Task-specific helpers (used by pipelines) ─────────────────────────────────

_CAPTION_SYSTEM = (
    "Write a short, engaging social media caption about the given business or topic. "
    "Be specific and direct. Max 3 sentences. No hashtags."
)

_FACTS_SYSTEM = (
    "Extract durable atomic facts from the exchange. "
    "Return a JSON array of short fact strings. "
    "Return [] if nothing durable. Return only valid JSON, no prose."
)

_SUMMARY_SYSTEM = (
    "Summarize the following in 2-3 sentences. Be factual and specific. No fluff."
)


def caption(subject: str, data: dict) -> str | None:
    """Generate a social caption.
    Deliberately returns None — captions are public-facing and Claude quality
    is meaningfully better. This keeps compose_caption() always on Claude/template.
    """
    return None


def extract_facts(exchange: str) -> list[str] | None:
    """Extract facts via Foundation Models. Returns None to signal fallback to Claude."""
    import json, re
    raw = ask_or_none(exchange, _FACTS_SYSTEM)
    if raw is None:
        return None
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    payload = match.group(0) if match else raw
    try:
        parsed = json.loads(payload)
        if isinstance(parsed, list):
            return [str(f) for f in parsed if f]
    except (json.JSONDecodeError, ValueError):
        pass
    return []


def summarize(text: str) -> str | None:
    """Summarize text via Foundation Models. Returns None to signal fallback."""
    return ask_or_none(text, _SUMMARY_SYSTEM)
