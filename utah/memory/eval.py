"""RAG recall-quality eval — precision@k / recall@k / MRR on a labeled set.

The audit's "make memory a defensible product claim, not just a guard": no-fabrication is
enforced, but recall QUALITY was never measured. This is the harness. Pure (takes a
``recall_fn`` so it runs against the live store OR a fake), it scores a labeled set of
``{query, relevant}`` examples where ``relevant`` is the substrings a correct hit must
contain. Reports the standard IR metrics so a regression in recall is caught, and the
no-fab RAG can be claimed as *measured*, not just intended.
"""
from __future__ import annotations

import json
import logging
import os

import msgspec

log = logging.getLogger("utah.memory.eval")


class RecallMetrics(msgspec.Struct, frozen=True):
    queries: int
    precision_at_k: float   # mean fraction of the top-k hits that are relevant
    recall_at_k: float      # mean fraction of a query's relevant items found in the top-k
    hit_rate: float         # fraction of queries with ≥1 relevant hit in the top-k
    mrr: float              # mean reciprocal rank of the first relevant hit
    k: int


def _is_relevant(hit_text: str, relevant: "list[str]") -> bool:
    """A hit is relevant if it contains ANY of the query's expected substrings (case-fold)."""
    low = (hit_text or "").casefold()
    return any(r.casefold() in low for r in relevant)


def _clean_terms(relevant) -> list[str]:
    """Relevant terms as non-empty strings. ``''`` is a substring of EVERYTHING — an
    empty/None term would mark every hit relevant and fake a perfect score, so blank
    terms are dropped and non-strings (e.g. a bare number) are coerced."""
    out: list[str] = []
    for term in relevant or ():
        text = "" if term is None else str(term)
        if text.strip():
            out.append(text)
    return out


def evaluate_recall(labeled, recall_fn, *, k: int = 5) -> RecallMetrics:
    """Score *recall_fn* over *labeled* = ``[{"query": str, "relevant": [str, ...]}, ...]``.

    ``recall_fn(query, k) -> list`` returns hit objects/dicts; each hit's text is read from
    a ``.content`` attribute or a ``"content"`` key. Empty labeled set → all-zero metrics.

    Raises:
        ValueError: ``k < 1`` (a top-0 metric is meaningless, never silently zero).
        TypeError: a non-dict example, named by index so the bad row is findable.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    labeled = list(labeled or [])
    for i, ex in enumerate(labeled):
        if not isinstance(ex, dict):
            raise TypeError(f"labeled[{i}] must be a dict, got {type(ex).__name__}")
    n = len(labeled)
    if n == 0:
        return RecallMetrics(0, 0.0, 0.0, 0.0, 0.0, k)

    p_sum = r_sum = hit_sum = mrr_sum = 0.0
    for ex in labeled:
        query = ex.get("query", "")
        relevant = _clean_terms(ex.get("relevant"))
        try:
            hits = list(recall_fn(query, k) or [])[:k]
        except Exception as exc:  # noqa: BLE001 — a recall failure scores 0 for that query, never aborts
            # ...but never SILENTLY: a misconfigured recall_fn scoring 0 invisibly
            # would let a CI gate "pass" on a broken store. Name the query and cause.
            log.warning("recall_fn failed for query %r: %s", str(query)[:120], exc)
            hits = []
        texts = [h.get("content", "") if isinstance(h, dict) else getattr(h, "content", None)
                 for h in hits]
        # recall_fn output is untrusted: non-str content (int, bytes, None) must be
        # coerced — one bad hit used to AttributeError the whole eval, not score 0.
        texts = [t if isinstance(t, str) else ("" if t is None else str(t)) for t in texts]
        rel_flags = [_is_relevant(t, relevant) for t in texts]
        n_rel_hits = sum(rel_flags)
        p_sum += (n_rel_hits / len(texts)) if texts else 0.0
        # recall@k: how many of the query's DISTINCT relevant items appear in the top-k
        if relevant:
            found = sum(1 for rterm in relevant
                        if any(rterm.casefold() in t.casefold() for t in texts))
            r_sum += found / len(relevant)
        hit_sum += 1.0 if n_rel_hits > 0 else 0.0
        for rank, flag in enumerate(rel_flags, start=1):
            if flag:
                mrr_sum += 1.0 / rank
                break

    return RecallMetrics(
        queries=n,
        precision_at_k=round(p_sum / n, 4),
        recall_at_k=round(r_sum / n, 4),
        hit_rate=round(hit_sum / n, 4),
        mrr=round(mrr_sum / n, 4),
        k=k,
    )


def run_eval(labeled=None, *, k: int = 5) -> dict:
    """Run the eval against the LIVE memory store (``memory.recall``) and return the
    metrics as a plain JSON-safe dict. Consumed today by the test suite and by the
    ``main`` CLI gate below (``python -m utah.memory.eval``); the shape is stable
    for any future deck panel. Raises like ``evaluate_recall`` on malformed input —
    a gate must fail loudly on a bad labeled set, never report zeros for it."""
    from utah import memory

    metrics = evaluate_recall(labeled or [], lambda q, kk: memory.recall(q, k=kk), k=k)
    return msgspec.structs.asdict(metrics)


def _env_min_hit_rate() -> float:
    """Gate threshold from ``UTAH_EVAL_MIN_HIT_RATE``; garbage degrades to 0.0
    (report-only) rather than crashing the gate it is supposed to power."""
    raw = os.environ.get("UTAH_EVAL_MIN_HIT_RATE", "0")
    try:
        return float(raw)
    except ValueError:
        log.warning("UTAH_EVAL_MIN_HIT_RATE=%r is not a number; gating at 0.0", raw)
        return 0.0


def main(argv: list[str] | None = None) -> int:
    """The concrete consumer: a CI/cron recall gate over a labeled-set JSON file.

    ``python -m utah.memory.eval labeled.json [--k N] [--min-hit-rate X]`` prints
    one JSON object (the ``run_eval`` metrics plus ``ok``) and exits:
        0 — hit_rate cleared the gate, 1 — measured but below the gate,
        2 — the labeled file is unreadable/malformed (never a silent pass).
    The threshold falls back to ``UTAH_EVAL_MIN_HIT_RATE`` (default 0 = report-only).
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="utah.memory.eval",
        description="Recall-quality gate: precision@k / recall@k / MRR over a labeled set.")
    parser.add_argument("labeled", help="path to a JSON list of {query, relevant} examples")
    parser.add_argument("--k", type=int, default=5, help="top-k cutoff (default 5)")
    parser.add_argument("--min-hit-rate", type=float, default=None,
                        help="gate: exit 1 below this hit_rate (default: env or 0)")
    args = parser.parse_args(argv)
    threshold = _env_min_hit_rate() if args.min_hit_rate is None else args.min_hit_rate

    try:
        with open(args.labeled, encoding="utf-8") as fh:
            labeled = json.load(fh)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(json.dumps({"ok": False, "error": f"unreadable labeled set: {exc}"[:300]}))
        return 2
    if not isinstance(labeled, list):
        print(json.dumps({"ok": False,
                          "error": f"labeled set must be a JSON list, got {type(labeled).__name__}"}))
        return 2
    try:
        out = run_eval(labeled, k=args.k)
    except (TypeError, ValueError) as exc:  # bad example shape / bad k — loud, bounded
        print(json.dumps({"ok": False, "error": str(exc)[:300]}))
        return 2
    out["ok"] = out["hit_rate"] >= threshold
    print(json.dumps(out))
    return 0 if out["ok"] else 1


if __name__ == "__main__":  # pragma: no cover — exercised via main() in tests
    raise SystemExit(main())


__all__ = ["RecallMetrics", "evaluate_recall", "run_eval", "main"]
