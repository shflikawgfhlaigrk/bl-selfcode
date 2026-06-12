"""Codebase self-knowledge — ingest Utah's own source into memory as ``source='code'``.

Why: the brain had NO way to ground answers about its own implementation. Memory held
facts/knowledge/core but no code, and the on-demand file-read path (an LSP tool shelling
to ``pyright-langserver``) wasn't installed — so "what does run_scheduled do?" honestly
returned "I don't know". This makes the *entire* tree retrievable grounding: every
top-level function/class becomes one embedded chunk, plus a per-module header chunk. The
brain's recall then surfaces the actual current source for any code question.

Migration contract (MIG-3 rework, 2026-06-12) — this is the only UNATTENDED cron
migration (``com.utah.codeindex``, daily), so it carries the full validate/atomic/verify
shape instead of the old delete-then-hope:

* **Input validation** — unreadable (OSError), empty, and mid-edit (SyntaxError) files
  are skipped and COUNTED honestly (``skipped`` in the result), never silently absent.
* **Atomic swap** — the old ingest DELETEd every ``code`` row on a separate autocommit
  connection BEFORE any insert, so a crash mid-ingest left the brain with ZERO code
  self-knowledge until the next day (and a partial run left a silent subset). Now
  delete + reinsert run in ONE statement-bounded transaction (``SET LOCAL
  statement_timeout`` — the :func:`migrations.ace_knowledge.rollback_inserted` shape):
  a crash at ANY point rolls back and leaves the PREVIOUS index intact. Embeddings are
  computed BEFORE the transaction, so the slow part never holds the swap open.
* **Reconciliation** — after commit, the committed ``source='code'`` row count must
  equal the chunks staged, per file too; any mismatch is documented to the failure log
  and reported as ``ok: False``.
* **Idempotent** — per-name constant chunk keys (``rel::name``) + full swap mean the
  same tree always produces the same rows, no dupes, on every re-run.
* **Never raises** — the cron boundary returns ``{ok: False, error: ...}`` and records
  the failure instead of crashing (no swallowed exceptions on the primary path: every
  abort is counted, logged, and documented).
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Callable, Sequence

from utah import config, failures
from utah import embed as _embed

log = logging.getLogger("utah.codebase")

#: utah/ package root; ingest everything under it (skip caches/tests live in tests/).
ROOT = Path(__file__).resolve().parent
MAX = config.MAX_CONTENT_CHARS

#: One staged memory row, ready for the atomic swap:
#: ``(content, tags, confidence, embedding)`` with ``tags == [rel, symbol]``.
CodeRow = tuple[str, list[str], float, list[float]]


def _chunks_for_file(src: str, rel: str) -> list[tuple[str, str]]:
    """``(symbol, text)`` chunks for one module: a header (path + module docstring) plus
    one chunk per top-level function/class, each labelled ``rel::name`` and carrying the
    real source segment so the brain answers from actual code.

    Raises ``SyntaxError`` on a mid-edit file — the caller skips it and counts the skip
    honestly (one bad file must not abort the run, but must never vanish silently)."""
    tree = ast.parse(src)
    doc = ast.get_docstring(tree) or ""
    chunks: list[tuple[str, str]] = [(f"{rel}::module", f"FILE {rel}\n{doc}".strip()[:MAX])]
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


def _collect(root: Path) -> tuple[list[tuple[str, str, str]], dict[str, int]]:
    """Validated ``(rel, symbol, text)`` chunks for the tree + honest skip counts.

    Input validation: unreadable (OSError) and empty/whitespace-only files are skipped
    and counted, as are mid-edit files that do not parse. Every parseable non-empty file
    yields at least its header chunk, so "ingested file" ⇔ "has chunks"."""
    skipped = {"unreadable": 0, "empty": 0, "syntax": 0}
    staged: list[tuple[str, str, str]] = []
    files = sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    for p in files:
        rel = str(p.relative_to(root.parent))  # e.g. "utah/product/probate.py"
        try:
            src = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            skipped["unreadable"] += 1
            log.warning("codebase: skip %s (unreadable: %s)", rel, exc)
            continue
        if not src.strip():
            skipped["empty"] += 1
            log.warning("codebase: skip %s (empty)", rel)
            continue
        try:
            chunks = _chunks_for_file(src, rel)
        except SyntaxError as exc:
            skipped["syntax"] += 1
            log.warning("codebase: skip %s (syntax: %s)", rel, exc)
            continue
        staged.extend((rel, symbol, text) for symbol, text in chunks)
    return staged, skipped


def _atomic_swap(rows: Sequence[CodeRow]) -> int:
    """Replace ALL ``source='code'`` rows with *rows* in ONE bounded transaction.

    Delete + reinsert commit together — a crash at any point (including mid-insert)
    rolls the whole swap back, so the previous index survives intact. Bounded I/O on
    every side: pooled connection (``connect_timeout`` + checkout timeout, the same
    pgvector pool the memory store uses) and ``SET LOCAL statement_timeout`` that dies
    with the transaction (the :func:`migrations.ace_knowledge.rollback_inserted` shape).
    Returns the number of prior rows cleared. Raises on any DB error — :func:`ingest`
    catches, documents, and reports honestly."""
    from utah import db_pool

    try:
        from pgvector import Vector
    except ImportError:  # older pgvector-python layouts
        from pgvector.utils import Vector

    with db_pool.vector_pool(config.DB_DSN).connection() as conn:
        with conn.transaction():
            conn.execute(
                f"SET LOCAL statement_timeout = {int(config.DB_STATEMENT_TIMEOUT_MS)}")
            cleared = conn.execute("DELETE FROM memory WHERE source='code'").rowcount or 0
            for content, tags, confidence, embedding in rows:
                conn.execute(
                    "INSERT INTO memory (content, source, tags, confidence, embedding) "
                    "VALUES (%s, 'code', %s, %s, %s)",
                    (content, list(tags), float(confidence), Vector(list(embedding))),
                )
    return cleared


def _code_counts() -> tuple[int, dict[str, int]]:
    """Committed ``source='code'`` rows: ``(total, {rel: count})`` — the reconciliation
    read-back. ``tags[1]`` is the file path (first tag) on every row this module writes;
    a foreign/odd row groups under its own key and is caught by the total check."""
    from utah import db_pool

    with db_pool.vector_pool(config.DB_DSN).connection() as conn:
        with conn.transaction():
            conn.execute(
                f"SET LOCAL statement_timeout = {int(config.DB_STATEMENT_TIMEOUT_MS)}")
            grouped = conn.execute(
                "SELECT tags[1], count(*) FROM memory WHERE source='code' GROUP BY tags[1]"
            ).fetchall()
    per_file = {(r[0] if r[0] is not None else ""): int(r[1]) for r in grouped}
    return sum(per_file.values()), per_file


def ingest(
    root: Path | None = None,
    *,
    embed_fn: Callable[[str], Sequence[float]] | None = None,
    swap_fn: Callable[[Sequence[CodeRow]], int] | None = None,
    counts_fn: Callable[[], tuple[int, dict[str, int]]] | None = None,
) -> dict:
    """Re-index the tree under *root* into memory as ``source='code'``.

    Validate -> embed (all of it, before any write) -> atomic swap (one transaction)
    -> reconcile the committed rows against what was staged. NEVER raises (the
    ``com.utah.codeindex`` cron prints this dict); every abort path records to the
    failure log and returns ``ok: False`` with the previous index intact wherever the
    swap did not commit. Returns
    ``{ok, files, chunks, cleared, skipped, error}`` (``files``/``chunks``/``cleared``
    keep their original meaning: files ingested, rows written, prior rows replaced).

    The three seams (*embed_fn*, *swap_fn*, *counts_fn*) default to the real embedder,
    the pooled atomic swap, and the pooled count read-back; tests inject fakes."""
    root = root or ROOT
    embed_fn = embed_fn or _embed.embed
    swap_fn = swap_fn or _atomic_swap
    counts_fn = counts_fn or _code_counts
    result: dict = {"ok": False, "files": 0, "chunks": 0, "cleared": 0,
                    "skipped": {"unreadable": 0, "empty": 0, "syntax": 0}, "error": None}

    def _fail(kind: str, detail: str) -> dict:
        failures.record("codebase", kind, detail)
        log.error("codebase ingest %s: %s", kind, detail)
        result["error"] = f"{kind}: {detail}"
        return result

    try:
        staged, skipped = _collect(root)
        result["skipped"] = skipped
        expected: dict[str, int] = {}
        for rel, _symbol, _text in staged:
            expected[rel] = expected.get(rel, 0) + 1
        result["files"] = len(expected)
        if not staged:
            # Refuse to wipe: an empty or missing root must never erase the live index.
            return _fail("empty_ingest", f"no ingestable chunks under {root} "
                                         f"(skipped {skipped}); index left untouched")

        rows: list[CodeRow] = []
        for rel, symbol, text in staged:
            try:
                rows.append((text, [rel, symbol], 1.0, [float(x) for x in embed_fn(text)]))
            except Exception as exc:  # noqa: BLE001 — abort BEFORE any write, honestly
                return _fail("embed_failed", f"{symbol}: {exc} "
                                             "(aborted before swap; previous index intact)")

        try:
            result["cleared"] = int(swap_fn(rows))
        except Exception as exc:  # noqa: BLE001 — the transaction rolled back whole
            return _fail("swap_failed", f"{exc} "
                                        "(transaction rolled back; previous index intact)")
        result["chunks"] = len(rows)

        try:
            total, per_file = counts_fn()
        except Exception as exc:  # noqa: BLE001 — committed, but unverified is not ok
            return _fail("reconcile_error", f"swap committed but unverified: {exc}")
        bad = [f"{rel}={per_file.get(rel, 0)}!={n}"
               for rel, n in sorted(expected.items()) if per_file.get(rel, 0) != n]
        if total != len(rows) or bad:
            return _fail("reconcile_mismatch",
                         f"committed {total} rows != {len(rows)} staged; "
                         f"per-file: {', '.join(bad[:5]) if bad else 'ok'}")

        result["ok"] = True
        log.info("codebase ingest: %d files -> %d chunks (cleared %d prior, skipped %s)",
                 result["files"], result["chunks"], result["cleared"], skipped)
        return result
    except Exception as exc:  # noqa: BLE001 — cron boundary: report, never raise
        return _fail("ingest_failed", repr(exc))


__all__ = ["ingest", "ROOT"]
