"""Mark Douglas — *Trading in the Zone*: the Five Fundamental Truths and the Seven
Principles of Consistency, seeded as durable facts.

The quick local model half-hallucinated these in testing, so the REAL text is
admitted as ``source="fact"`` (high confidence) and answered by recall — not by a
model's flaky parametric memory. Seeding is idempotent: memory's dedup reinforces
existing rows instead of duplicating on re-seed.

Run: ``python -m utah.knowledge.douglas``
"""
from __future__ import annotations

import logging
import re
from typing import Callable

log = logging.getLogger("utah.knowledge.douglas")

#: The Five Fundamental Truths (Trading in the Zone, ch. 7), verbatim.
FIVE_TRUTHS: list[str] = [
    "Anything can happen.",
    "You don't need to know what is going to happen next in order to make money.",
    "There is a random distribution between wins and losses for any given set of "
    "variables that define an edge.",
    "An edge is nothing more than an indication of a higher probability of one "
    "thing happening over another.",
    "Every moment in the market is unique.",
]

#: The Seven Principles of Consistency (the consistent trader's belief set), verbatim.
SEVEN_PRINCIPLES: list[str] = [
    "I objectively identify my edges.",
    "I predefine the risk of every trade.",
    "I completely accept the risk or I am willing to let go of the trade.",
    "I act on my edges without reservation or hesitation.",
    "I pay myself as the market makes money available to me.",
    "I continually monitor my susceptibility for making errors.",
    "I understand the absolute necessity of these principles of consistent success "
    "and, therefore, I never violate them.",
]

_SUMMARY = (
    "Mark Douglas's trading rules (from his book Trading in the Zone) are the Five "
    "Fundamental Truths and the Seven Principles of Consistency — a probabilistic, "
    "discipline-first mindset for trading."
)

#: Every fact, phrased so recall matches "mark douglas trading rules".
FACTS: list[str] = (
    [_SUMMARY]
    + [f"Mark Douglas fundamental truth {i}: {t}" for i, t in enumerate(FIVE_TRUTHS, 1)]
    + [f"Mark Douglas principle of consistency {i}: {p}" for i, p in enumerate(SEVEN_PRINCIPLES, 1)]
)


#: A query is about this pack when it names the author or the work. Kept precise
#: so it never hijacks an unrelated "my trading rules" question.
_MATCH = re.compile(
    r"\b(mark\s+douglas|douglas'?s?\s+(rules|truths|principles|trading)|"
    r"trading in the zone|fundamental truths|principles of consistency)\b",
    re.I,
)


def matches(query: str) -> bool:
    """True when *query* is asking about this curated pack (router → KNOWLEDGE)."""
    return bool(_MATCH.search(query or ""))


def render() -> str:
    """The REAL pack, assembled verbatim. Deterministic — never a model's recall."""
    lines = [
        "Mark Douglas's trading rules (from Trading in the Zone):",
        "",
        "The Five Fundamental Truths:",
    ]
    lines += [f"{i}. {t}" for i, t in enumerate(FIVE_TRUTHS, 1)]
    lines += ["", "The Seven Principles of Consistency:"]
    lines += [f"{i}. {p}" for i, p in enumerate(SEVEN_PRINCIPLES, 1)]
    return "\n".join(lines)


def answer(query: str) -> str | None:
    """The verbatim pack if *query* is about it, else ``None`` (no match → other tiers)."""
    return render() if matches(query) else None


def seed(*, store: Callable | None = None) -> int:
    """Admit every fact as durable memory. Returns the count stored. A single
    admission failure is logged and skipped, and a dead/unimportable memory
    backend yields 0 — never a traceback (best-effort boundary)."""
    if store is None:
        try:
            from utah import memory

            store = memory.store
        except Exception as exc:  # noqa: BLE001 — boundary: a broken backend seeds 0
            log.warning("douglas: memory backend unavailable, nothing seeded: %s", exc)
            return 0
    stored = 0
    for fact in FACTS:
        try:
            store(fact, source="fact", confidence=0.95)
            stored += 1
        except Exception as exc:  # noqa: BLE001 — one bad row never kills the seed
            log.warning("douglas: could not store a fact (skipped): %s", exc)
    return stored


def main() -> int:
    """CLI seed. Exit 0 only when EVERY fact landed — a partial or zero seed is a
    nonzero exit, so cron/operators see the failure instead of a fake success."""
    logging.basicConfig(level=logging.INFO)
    n = seed()
    print(f"douglas: seeded {n}/{len(FACTS)} facts")
    return 0 if n == len(FACTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
