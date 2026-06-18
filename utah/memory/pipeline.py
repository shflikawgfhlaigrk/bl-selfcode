"""Memory pipeline — the one orchestration path every caller uses."""
from __future__ import annotations

import logging
import threading
from typing import Sequence

from utah import config, entities
from utah.embed import EmbedError, embed
from utah.memory.exceptions import AdmissionDenied, MemoryUnavailable
from utah.memory.logic import (
    decide_write,
    entity_grounds,
    is_canspam_address_query,
    is_directive,
    is_untrusted_canspam_content,
    lexical_overlap,
    passes_gate,
    recall_pool,
    rrf_fuse,
)
from utah.memory.store import PostgresStore
from utah.memory.types import DenseRow, StoreBackend
from utah.objects import Hit, WriteAction, WriteResult
from utah.rerank import rerank

log = logging.getLogger("utah.memory")

_backend: StoreBackend | None = None
_backend_lock = threading.Lock()


def get_backend() -> StoreBackend:
    """Return the process-global backend, creating PostgresStore lazily."""
    global _backend
    with _backend_lock:
        if _backend is None:
            _backend = PostgresStore()
        return _backend


def set_backend(backend: StoreBackend | None) -> None:
    """Inject a backend (tests). ``None`` restores the default PostgresStore."""
    global _backend
    with _backend_lock:
        if _backend is not None and backend is not _backend:
            try:
                _backend.close()
            except Exception as exc:  # closing must never mask the swap
                log.warning("error closing previous backend: %s", exc)
        _backend = backend


def init() -> None:
    """Create the schema (idempotent; safe to run on every boot)."""
    get_backend().init_schema()


def reset() -> None:
    """Dev-only: drop and re-create the schema (destructive, explicit)."""
    get_backend().reset_schema()


def store(
    content: str,
    source: str = "user",
    tags: Sequence[str] | None = None,
    confidence: float = 0.6,
) -> WriteResult:
    """Admission-gated write: embed + source, de-dup, supersede on contradiction.

    Raises:
        AdmissionDenied: empty content, disallowed source, oversize content,
            or confidence out of [0, 1] — policy rejections, by design.
        EmbedError: the content could not be embedded (a row is never
            admitted without a vector — that is the gate).
        MemoryUnavailable: the store is down.
    """
    text = (content or "").strip()
    if not text:
        raise AdmissionDenied("admission denied: empty content")
    if len(text) > config.MAX_CONTENT_CHARS:
        raise AdmissionDenied(
            f"admission denied: content over {config.MAX_CONTENT_CHARS} chars (chunk it)"
        )
    if source not in config.ALLOWED_SOURCES:
        raise AdmissionDenied(
            f"admission denied: source {source!r} not in {sorted(config.ALLOWED_SOURCES)}"
        )
    if not 0.0 <= confidence <= 1.0:
        raise AdmissionDenied(f"admission denied: confidence {confidence} not in [0, 1]")
    # Confabulation guard: a durable fact must be a FACT — not a stored conversation turn
    # (``Q: …\nA: …``) nor a dead old-AceOS code dump (``[acesd/…]``). Those exact shapes
    # flooded the fact source (3,340 junk rows purged 2026-06-08 — they surfaced rap lyrics
    # and mic-garble in recall). The 'turn' source is legitimately Q/A, so it is exempt.
    if source != "turn":
        head = text.lstrip()
        if head.startswith("[acesd/") or (head.startswith("Q:") and "\nA:" in text):
            raise AdmissionDenied(
                "admission denied: turn-shaped or dead-code content is not a durable fact")
    # Profile poison gate: adversarial CAN-SPAM/address facts must not land at high
    # confidence via remember_profile (stress row 42048: "123 Fake St" at 0.9).
    if source in ("user", "fact") and is_untrusted_canspam_content(text):
        raise AdmissionDenied(
            "admission denied: untrusted CAN-SPAM/address profile fact")

    vector = embed(text)  # EmbedError propagates: no vector, no admission
    backend = get_backend()
    neighbors = backend.nearest(vector, config.SUPERSEDE_SCAN)
    decision = decide_write(text, neighbors)
    if decision.action is WriteAction.REINFORCED:
        if decision.reinforce_id is None:
            # Not an assert: python -O strips asserts, and reinforce(None) would
            # silently corrupt a row. The invariant breaking must fail LOUDLY.
            raise RuntimeError(
                "reinforce decision carried no target id (decide_write invariant broken)"
            )
        backend.reinforce(decision.reinforce_id)
        return WriteResult(id=decision.reinforce_id, action=WriteAction.REINFORCED)
    mem_id = backend.insert(
        content=text,
        source=source,
        tags=tuple(tags or ()),
        confidence=confidence,
        embedding=vector,
        entity_names=entities.extract(text),
        supersede_ids=decision.supersede_ids,
    )
    if decision.supersede_ids:
        log.info("memory %d supersedes %s", mem_id, decision.supersede_ids)
    return WriteResult(
        id=mem_id, action=WriteAction.INSERTED, superseded=list(decision.supersede_ids)
    )


def _touch_off_path(ids: list[int]) -> None:
    """Bump frequency/recency for the recalled rows OFF the read hot path. touch() is a
    serialized WRITE under the store's connection RLock; inline it made every recall pay
    a write and block concurrent reads (a measured cold-path cost). Fire it on a daemon
    thread so recall returns immediately — the signal is best-effort, never blocks a read."""
    if not ids:
        return

    def _run() -> None:
        try:
            get_backend().touch(ids)
        except Exception as exc:  # noqa: BLE001 — best-effort signal, never fatal
            log.warning("recall touch failed (non-fatal): %s", exc)

    threading.Thread(target=_run, daemon=True).start()


def recall(query: str, k: int = config.RECALL_K) -> list[Hit]:
    """Hybrid recall: dense + sparse -> RRF(k=60) -> rerank -> entity boost.

    Superseded and archived rows are structurally excluded (in SQL). If the
    embedder is down, degrades to sparse-only (logged); if the reranker is
    down, ranking rests on RRF order + entity boost. Raises
    :class:`MemoryUnavailable` only when the store itself is unreachable.
    """
    query = (query or "").strip()
    if not query:
        return []
    backend = get_backend()
    pool = recall_pool(k)

    dense: list[DenseRow] = []
    curated: list[DenseRow] = []
    code: list[DenseRow] = []
    try:
        vector = embed(query)
        dense = backend.dense_search(vector, pool)
        # Curated lane: the nearest identity/library rows ALWAYS enter the pool (their
        # own RRF list), so they reach the reranker even when the general pool is full
        # of facts — the diagnosed miss (a relevant Law never reaching rerank).
        curated = backend.curated_search(vector, config.CURATED_LANE_K, tuple(config.CURATED_SOURCES))
        # Code lane: the nearest source-code chunks get their OWN guaranteed slot so a
        # code/self question surfaces the relevant FUNCTION even when chatty 'turn' rows
        # swamp the general pool — the diagnosed miss where the brain couldn't name its
        # own functions because their chunks never reached the reranker.
        code = backend.curated_search(vector, config.CODE_LANE_K, config.CODE_SOURCES)
    except EmbedError as exc:
        log.warning("dense lane down (embed failed), sparse-only recall: %s", exc)
    sparse = backend.sparse_search(query, pool)

    fused = rrf_fuse([[r.id for r in dense], [r.id for r in sparse],
                      [r.id for r in curated], [r.id for r in code]])
    if not fused:
        return []
    meta: dict[int, tuple[str, str]] = {r.id: (r.content, r.source) for r in dense}
    for r in (*sparse, *curated, *code):
        meta.setdefault(r.id, (r.content, r.source))
    sims: dict[int, float] = {r.id: r.sim for r in dense}
    for r in (*curated, *code):
        sims.setdefault(r.id, r.sim)

    candidates = sorted(fused, key=lambda i: fused[i], reverse=True)[:pool]
    scores = rerank(query, [meta[i][0] for i in candidates])  # degrades to zeros

    query_ents = entities.normalized_set(query)
    boosts: dict[int, float] = {}
    if query_ents:
        mem_ents = backend.entity_names(candidates)
        for cid in candidates:
            if query_ents & mem_ents.get(cid, set()):
                boosts[cid] = config.ENTITY_BOOST

    ranked = sorted(  # stable: ties keep RRF order
        zip(candidates, scores),
        key=lambda pair: (
            pair[1]
            + boosts.get(pair[0], 0.0)                                   # entity (GraphRAG) boost
            + config.SOURCE_BOOST.get(meta[pair[0]][1], 0.0)             # source-authority prior
        ),
        reverse=True,
    )
    top = ranked[:k]

    _touch_off_path([i for i, _ in top])  # frequency/recency WRITE — never on the read path

    return [
        Hit(
            id=i,
            content=meta[i][0],
            source=meta[i][1],
            score=round(float(s) + boosts.get(i, 0.0), 4),
            sim=round(sims.get(i, 0.0), 4),
        )
        for i, s in top
    ]


def core_recall() -> list[Hit]:
    """Return all live ``source='core'`` rows — identity/standing facts always
    injected ahead of RAG hits so the brain never loses Michael's ground truth.

    Returns [] (never raises) — the brain loop must never break because core
    facts are temporarily unreadable.
    """
    try:
        rows = get_backend().core_rows()
        return [Hit(id=int(r[0]), content=r[1], source=r[2], score=1.0, sim=1.0) for r in rows]
    except Exception as exc:
        log.warning("core_recall failed (degrading to empty): %s", exc)
        return []


def answer(query: str, k: int = config.RECALL_K) -> tuple[str | None, list[Hit]]:
    """No-fabrication gate: answer from memory only when confidently grounded.

    Returns ``(text, hits)`` when the best hit is semantically close
    (``sim >= ANSWER_MIN_SIM``), lexically on-topic (``overlap >=
    ANSWER_MIN_OVERLAP``), AND not about a different entity than the query
    (:func:`entity_grounds`); otherwise ``(None, hits)`` and the caller falls to the
    brain with the hits as context. The entity guard is what stops a confident-looking
    but wrong-subject fact (an Everest fact for a Kilimanjaro question) being served.
    """
    hits = recall(query, k)
    if not hits:
        return None, []
    candidates = hits
    if is_canspam_address_query(query):
        trusted = [h for h in hits if not is_untrusted_canspam_content(h.content)]
        if not trusted:
            return None, hits
        candidates = trusted
    best = candidates[0]
    # A conversational TURN is a record of a PAST exchange — keep it as CONTEXT for the
    # brain, but never re-serve it verbatim as the authoritative answer. Echoing a past
    # hedge/refusal ossifies it and bypasses fresh reasoning over NEWER context (e.g. the
    # now-indexed source code): that is exactly why a self/code question kept replaying an
    # old "I can't name it" turn. Only durable sources shortcut; a turn falls to the brain.
    if getattr(best, "source", "") == "turn":
        return None, hits
    # A DIRECTIVE ("When asked X, do Y", "Do not invent…", "Never state…") is guidance
    # about HOW to answer, not an answer. Re-serving it verbatim makes Ace recite the
    # instruction ("pull from the trade log…") instead of following it — the live "how is
    # trading doing today" bug. Keep it as CONTEXT for the brain; same shape as the 'turn'
    # guard above. (Many such rows are mislabeled source='fact' in the live store.)
    if is_directive(best.content):
        return None, hits
    if not passes_gate(best.sim, lexical_overlap(query, best.content)):
        return None, hits
    if entity_grounds(query, best.content) is False:
        return None, hits  # best hit is about a DIFFERENT entity → don't answer it
    return best.content, hits


def list_memories(limit: int = 50, offset: int = 0) -> list[dict]:
    """Live memory rows behind the gauge (deck drill-down). Raises MemoryUnavailable."""
    return get_backend().list_memories(limit, offset)


def list_entities(limit: int = 100) -> list[dict]:
    """Entities behind the gauge (deck drill-down). Raises MemoryUnavailable."""
    return get_backend().list_entities(limit)


def decay() -> int:
    """Run the decay pass: rescore live rows, archive faded unprotected rows.

    Never deletes (zones are reversible). Returns rows archived this pass.
    """
    return get_backend().apply_decay()


def close() -> None:
    """Close the active backend (idempotent; safe at shutdown)."""
    global _backend
    with _backend_lock:
        if _backend is not None:
            _backend.close()
            _backend = None


# Only the names this module OWNS: pure-logic rules live in utah.memory.logic and
# types/exceptions in their own modules — the package facade re-exports the union.
# (A previous __all__ listed five names never imported here, breaking star-imports.)
__all__ = [
    "answer",
    "close",
    "core_recall",
    "decay",
    "get_backend",
    "init",
    "list_entities",
    "list_memories",
    "recall",
    "reset",
    "set_backend",
    "store",
]
