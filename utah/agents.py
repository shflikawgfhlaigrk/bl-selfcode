"""Hot-loaded single-file agents — Ace's self-build surface.

Ace reliably self-builds exactly ONE file: an agent exposing ``KEYWORDS`` (trigger
vocabulary) and ``run(ctx)`` that returns a string answer or ``None`` to decline. This
loader scans ``agents/`` at the repo root, loads each, and routes a turn to the first
agent whose keyword matches and whose ``run`` returns non-None. So a NEW capability is
one file Ace writes — no router edit, no core edit, no multi-file codegen (the thing his
brain times out on).

Safety: an agent that fails to load or raises is skipped, never crashes Ace. The dir is
gated — only Ace's test-passed self-code merges (or a human review) land a file here.
Honest by contract: an agent returns None to decline, so the turn falls through to the
brain — it never fabricates to "win" a turn.
"""
from __future__ import annotations

import importlib.util
import logging
import pathlib

log = logging.getLogger(__name__)

_DIR = pathlib.Path(__file__).resolve().parent.parent / "agents"


def _load(directory: pathlib.Path | None = None) -> list:
    """Return ``[(name, [keywords...], run_callable), ...]`` for every valid agent file."""
    d = directory or _DIR
    agents: list = []
    if not d.is_dir():
        return agents
    for p in sorted(d.glob("*.py")):
        if p.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"utah_agent_{p.stem}", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            kws = [str(k).lower() for k in (getattr(mod, "KEYWORDS", []) or [])]
            run = getattr(mod, "run", None)
            if kws and callable(run):
                agents.append((p.stem, kws, run))
            else:
                log.warning("agent %s skipped: needs KEYWORDS + run(ctx)", p.name)
        except Exception as exc:  # noqa: BLE001 — a broken agent must never break Ace
            log.warning("agent %s failed to load: %s", p.name, exc)
    return agents


_AGENTS: list = _load()


def reload() -> int:
    """Re-scan the dir (a fresh self-built agent becomes live). Returns the count."""
    global _AGENTS
    _AGENTS = _load()
    return len(_AGENTS)


def names() -> list:
    return [n for n, _, _ in _AGENTS]


def route(text: str):
    """First loaded agent whose keyword is in *text* AND whose ``run`` returns non-None;
    else ``None`` so the caller falls through to the brain. An agent that declines or
    raises is passed over — never crashes the turn, never fabricates."""
    t = (text or "").lower()
    ctx = {"message": text, "text": text, "query": text}
    for name, kws, run in _AGENTS:
        if any(k in t for k in kws):
            try:
                out = run(ctx)
            except Exception as exc:  # noqa: BLE001
                log.warning("agent %s run error: %s", name, exc)
                continue
            if out is not None and str(out).strip():
                return str(out)
    return None
