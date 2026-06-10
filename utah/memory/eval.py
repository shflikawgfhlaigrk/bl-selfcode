"""RAG recall-quality eval — precision@k / recall@k / MRR on a labeled set.

The audit's "make memory a defensible product claim, not just a guard": no-fabrication is
enforced, but recall QUALITY was never measured. This is the harness. Pure (takes a
``recall_fn`` so it runs against the live store OR a fake), it scores a labeled set of
``{query, relevant}`` examples where ``relevant`` is the substrings a correct hit must
contain. Reports the standard IR metrics so a regression in recall is caught, and the
no-fab RAG can be claimed as *measured*, not just intended.
"""
from __future__ import annotations

import msgspec


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
    return any((r or "").casefold() in low for r in relevant)


def evaluate_recall(labeled, recall_fn, *, k: int = 5) -> RecallMetrics:
    """Score *recall_fn* over *labeled* = ``[{"query": str, "relevant": [str, ...]}, ...]``.

    ``recall_fn(query, k) -> list`` returns hit objects/dicts; each hit's text is read from
    a ``.content`` attribute or a ``"content"`` key. Empty labeled set → all-zero metrics."""
    labeled = list(labeled or [])
    n = len(labeled)
    if n == 0:
        return RecallMetrics(0, 0.0, 0.0, 0.0, 0.0, k)

    p_sum = r_sum = hit_sum = mrr_sum = 0.0
    for ex in labeled:
        query = ex.get("query", "")
        relevant = list(ex.get("relevant") or [])
        try:
            hits = list(recall_fn(query, k) or [])[:k]
        except Exception:  # noqa: BLE001 — a recall failure scores 0 for that query, never aborts
            hits = []
        texts = [getattr(h, "content", None) if not isinstance(h, dict) else h.get("content", "")
                 for h in hits]
        texts = [t or "" for t in texts]
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
    """Run the eval against the LIVE memory store (``memory.recall``). Returns a plain dict
    for the deck / a CI gate. Pass *labeled* or rely on a caller-supplied set."""
    from utah import memory

    metrics = evaluate_recall(labeled or [], lambda q, kk: memory.recall(q, k=kk), k=k)
    return msgspec.structs.asdict(metrics)


__all__ = ["RecallMetrics", "evaluate_recall", "run_eval"]
