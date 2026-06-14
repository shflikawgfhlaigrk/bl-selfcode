"""Pure memory logic — no I/O, each rule has a direct unit test."""
from __future__ import annotations

import math
import re
from typing import Sequence

from utah import config, entities
from utah.memory.types import Neighbor
from utah.objects import WriteAction, WriteDecision

_STOP = frozenset(
    "the a an of to in is are was were be and or for on at with it this that "
    "what who whom how why when where do does did i you he she they we me my "
    "your his her our their if".split()
)


def content_words(text: str) -> list[str]:
    """Lowercased content words of *text* (stopwords and 1-char tokens out)."""
    return [
        w
        for w in re.findall(r"[a-z0-9]+", text.lower())
        if w not in _STOP and len(w) > 1
    ]


def lexical_overlap(query: str, content: str) -> float:
    """Fraction of the query's content words present (substring) in *content*."""
    words = content_words(query)
    if not words:
        return 0.0
    haystack = content.lower()
    return sum(1 for w in words if w in haystack) / len(words)


def passes_gate(sim: float, overlap: float) -> bool:
    """No-fabrication answer gate: BOTH thresholds must hold."""
    return sim >= config.ANSWER_MIN_SIM and overlap >= config.ANSWER_MIN_OVERLAP


def entity_grounds(query: str, content: str) -> bool | None:
    """Does the hit's entity actually appear in the query?

    Returns True when grounded, False when wrong entity, None when undecidable.
    """
    hit_entities = entities.normalized_set(content)
    if not hit_entities:
        return None
    q_tokens = set(re.findall(r"[a-z0-9]+", query.casefold()))
    return any(set(ent.split()).issubset(q_tokens) for ent in hit_entities)


def rrf_fuse(
    rankings: Sequence[Sequence[int]], k: int = config.RRF_K
) -> dict[int, float]:
    """Reciprocal-rank fusion: score(id) = sum over lanes of 1 / (k + rank)."""
    scores: dict[int, float] = {}
    for lane in rankings:
        for rank, item in enumerate(lane, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


def decide_write(content: str, neighbors: Sequence[Neighbor]) -> WriteDecision:
    """Deterministic write decision against the nearest live rows."""
    norm_new = " ".join(content.split()).casefold()
    for nb in neighbors:
        if nb.sim >= config.DEDUP_SIM or " ".join(nb.content.split()).casefold() == norm_new:
            return WriteDecision(action=WriteAction.REINFORCED, reinforce_id=nb.id)

    new_ents = entities.normalized_set(content)
    supersede: list[int] = []
    for nb in neighbors:
        if nb.source in config.NEVER_SUPERSEDE_SOURCES:
            continue
        if nb.sim >= config.SUPERSEDE_SIM:
            supersede.append(nb.id)
        elif nb.sim >= config.SUPERSEDE_ENT and new_ents & entities.normalized_set(nb.content):
            supersede.append(nb.id)
    return WriteDecision(action=WriteAction.INSERTED, supersede_ids=supersede)


def compute_decay(age_seconds: float, reinforcement: int, confidence: float) -> float:
    """Decay score: recency x frequency x confidence (mirrors the SQL exactly)."""
    recency = math.exp(-max(age_seconds, 0.0) / config.DECAY_HALFLIFE_SECONDS)
    frequency = min(1.0, math.log(1 + max(reinforcement, 0)) / 3.0)
    return (
        config.DECAY_W_RECENCY * recency
        + config.DECAY_W_FREQUENCY * frequency
        + config.DECAY_W_CONFIDENCE * confidence
    )


def should_archive(
    decay_score: float, age_seconds: float, source: str, superseded: bool
) -> bool:
    """Archive rule for the decay pass (reversible; never a delete)."""
    if superseded or source in config.DECAY_PROTECTED_SOURCES:
        return False
    if age_seconds < config.DECAY_MIN_AGE_DAYS * 86_400:
        return False
    return decay_score < config.DECAY_ARCHIVE_BELOW


def recall_pool(k: int) -> int:
    """Candidate pool size per lane before fusion/rerank."""
    return max(k * config.RECALL_POOL_FACTOR, config.RECALL_POOL_MIN)


# --- CAN-SPAM / physical-address provenance (J-034 / BLA-448) -----------------

_CANSPAM_PROFILE = re.compile(
    r"can-?spam|physical address|mailing address|postal address",
    re.I,
)
_CANSPAM_QUERY = re.compile(
    r"can-?spam|physical address|mailing address|postal address|business address",
    re.I,
)
_ADDR_STREET = re.compile(
    r"(\d+\s+[A-Za-z0-9\s.'-]+(?:Rd|Road|St|Street|Ave|Avenue|Dr|Drive|Blvd|Lane|Ln|Way)\.?)",
    re.I,
)
_UNTRUSTED_CANSPAM = re.compile(
    r"\bfake\b|123 fake|stress.?test|injected|spoofed?|example\.com|"
    r"placeholder|replace me|\[can-spam",
    re.I,
)


def is_canspam_address_query(query: str) -> bool:
    """True when *query* asks for a regulated CAN-SPAM / business mailing address."""
    return bool(_CANSPAM_QUERY.search(query or ""))


def is_canspam_regulated_content(text: str) -> bool:
    """True when *text* claims a CAN-SPAM or business mailing address."""
    t = text or ""
    if _CANSPAM_PROFILE.search(t):
        return True
    return bool(_ADDR_STREET.search(t) and re.search(r"address|mailing", t, re.I))


def is_untrusted_canspam_content(text: str) -> bool:
    """True when regulated address content looks adversarial or placeholder."""
    if not is_canspam_regulated_content(text):
        return False
    if _UNTRUSTED_CANSPAM.search(text):
        return True
    m = _ADDR_STREET.search(text)
    if m and not config._canspam_is_real(m.group(1).strip()):  # noqa: SLF001
        return True
    return False
