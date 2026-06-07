"""Codebase self-knowledge — ingest Utah's own source into memory as ``source='code'``.

Why: the brain had NO way to ground answers about its own implementation. Memory held
facts/knowledge/core but no code, and the on-demand file-read path (an LSP tool shelling
to ``pyright-langserver``) wasn't installed — so "what does run_scheduled do?" honestly
returned "I don't know". This makes the *entire* tree retrievable grounding: every
top-level function/class becomes one embedded chunk, plus a per-module header chunk. The
brain's recall then surfaces the actual current source for any code question.

Re-runnable: clears prior ``code`` rows and re-ingests, so it tracks the live tree
(run it after a SICA self-merge or any edit). Real source only — never fabricated.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

import psycopg

from utah import config
from utah import embed as _embed
from utah.memory import get_backend

log = logging.getLogger("utah.codebase")

#: utah/ package root; ingest everything under it (skip caches/tests live in tests/).
ROOT = Path(__file__).resolve().parent
MAX = config.MAX_CONTENT_CHARS


def _chunks_for_file(path: Path, rel: str) -> list[tuple[str, str]]:
    """``(symbol, text)`` chunks for one module: a header (path + module docstring) plus
    one chunk per top-level function/class, each labelled ``rel::name`` and carrying the
    real source segment so the brain answers from actual code."""
    src = path.read_text(encoding="utf-8", errors="replace")
    chunks: list[tuple[str, str]] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:  # a mid-edit file must not abort the whole ingest
        log.warning("codebase: skip %s (syntax: %s)", rel, exc)
        return chunks
    doc = ast.get_docstring(tree) or ""
    chunks.append((f"{rel}::module", f"FILE {rel}\n{doc}".strip()[:MAX]))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            seg = ast.get_source_segment(src, node) or ""
            if seg.strip():
                chunks.append((f"{rel}::{node.name}", f"FILE {rel} — {node.name}\n{seg}"[:MAX]))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            # Each NAMED module constant (DB_DSN, NATIONAL_CHAINS, ports, thresholds)
            # is its own focused chunk — so a query about that symbol surfaces a
            # high-signal hit, not a diluted all-constants blob.
            seg = ast.get_source_segment(src, node) or ""
            if not seg.strip():
                continue
            tgt = node.targets[0] if isinstance(node, ast.Assign) and node.targets else \
                getattr(node, "target", None)
            cname = tgt.id if isinstance(tgt, ast.Name) else "constants"
            chunks.append((f"{rel}::{cname}", f"FILE {rel} — {cname}\n{seg}"[:MAX]))
    return chunks


def ingest(root: Path | None = None) -> dict:
    """Embed + store every code chunk under *root* as ``source='code'`` (idempotent).
    Returns ``{files, chunks, cleared}``."""
    root = root or ROOT
    backend = get_backend()
    with psycopg.connect(config.DB_DSN, autocommit=True) as c:
        cleared = c.execute("DELETE FROM memory WHERE source='code'").rowcount
    files = sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    n = 0
    for p in files:
        rel = str(p.relative_to(root.parent))  # e.g. "utah/product/probate.py"
        for symbol, text in _chunks_for_file(p, rel):
            try:
                backend.insert(content=text, source="code", tags=(rel, symbol),
                               confidence=1.0, embedding=_embed.embed(text),
                               entity_names=(), supersede_ids=())
                n += 1
            except Exception as exc:  # noqa: BLE001 — one bad chunk must not abort
                log.warning("codebase: skip chunk %s: %s", symbol, exc)
    log.info("codebase ingest: %d files -> %d chunks (cleared %d prior)", len(files), n, cleared)
    return {"files": len(files), "chunks": n, "cleared": cleared}


__all__ = ["ingest", "ROOT"]
