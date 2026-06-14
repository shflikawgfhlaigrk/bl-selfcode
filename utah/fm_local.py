"""On-device LLM via Apple Foundation Models (free, no network, low RAM).

Bridges the Python daemon to the compiled Swift CLI (``native/fm_cli``), which calls
Apple's on-device model on Apple Silicon. This is the cheap local tier: use it for
fast-path work (routing, classification, short rewrites) instead of the RAM-heavy
ollama models; substantive reasoning still routes to the Claude CLI brain.

Honest by design — a model that is unavailable or errors raises, never returns junk
(exit 2 = model unavailable / Apple Intelligence off, 3 = generation error).
"""
from __future__ import annotations

import pathlib
import subprocess

_CLI = pathlib.Path(__file__).resolve().parent.parent / "native" / "fm_cli"


def cli_path() -> pathlib.Path:
    return _CLI


def available(timeout: float = 60.0) -> bool:
    """True only if the binary is built AND the on-device model answers a real ping."""
    if not _CLI.exists():
        return False
    try:
        p = subprocess.run([str(_CLI)], input="reply with: ok", capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode == 0 and bool(p.stdout.strip())
    except (subprocess.SubprocessError, OSError):
        return False


def generate(prompt: str, timeout: float = 60.0) -> str:
    """Return the on-device model's response to *prompt*. Raises RuntimeError on a
    missing binary, unavailable model, timeout, or generation error — never silent."""
    if not prompt or not prompt.strip():
        raise ValueError("empty prompt")
    if not _CLI.exists():
        raise RuntimeError(
            f"fm_cli not built at {_CLI} "
            "(build: xcrun swiftc -O native/fm_cli.swift -o native/fm_cli)")
    try:
        p = subprocess.run([str(_CLI)], input=prompt, capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"on-device model timed out after {timeout}s") from exc
    except OSError as exc:
        raise RuntimeError(f"cannot run fm_cli: {exc}") from exc
    if p.returncode != 0:
        raise RuntimeError(
            f"on-device model failed (exit {p.returncode}): {p.stderr.strip() or 'no detail'}")
    return p.stdout.strip()
