"""Structural no-fabrication proof — verify an answer's facts trace to the CONTEXT.

No-fabrication was enforced by PROMPT (``NO_FAB``) + the ``is_refusal`` post-check — an
INTENTION, not a proof. This adds the structural check the audit asked for: after the brain
answers, verify each SALIENT factual token (numbers/dates/money — the highest-signal
fabrication) maps to a supporting span in the CONTEXT. An answer that asserts a number
absent from its context contains an unsupported fact. Pure + dependency-free.

Conservative by design: it flags NUMBERS (a fabricated stat/price/year is the dangerous
case) and reports an entity-coverage score advisorily. Default is ADVISORY (score + log);
``strict`` mode lets a caller DOWNGRADE an unsupported answer to "I don't know." — so the
guarantee can be turned from "promised" into "proven per answer" where it matters.
"""
from __future__ import annotations

import re

import msgspec

from utah import entities

#: Numbers worth checking: ≥2 digits (so "5 rules"/"a 3rd" don't false-flag), money, years.
_NUM = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")
#: Entities that are always "known" (persona/owner) — never count as unsupported.
_ALWAYS_KNOWN = frozenset({"ace", "michael", "utah", "i don't know"})


class AttributionReport(msgspec.Struct, frozen=True):
    supported: bool                 # no unsupported numbers (the hard gate)
    unsupported_numbers: list[str]  # numeric claims not found in the context
    unsupported_entities: list[str] # entity mentions not in the context (advisory)
    number_coverage: float          # fraction of checked numbers supported
    entity_coverage: float          # fraction of checked entities supported


def _norm_num(tok: str) -> str:
    """Comparable digit form: drop $ , % and leading/trailing punctuation; keep digits + dot."""
    return tok.replace("$", "").replace(",", "").replace("%", "").strip(". ")


def _checkable_numbers(text: str) -> list[str]:
    out: list[str] = []
    for m in _NUM.finditer(text or ""):
        norm = _norm_num(m.group(0))
        digits = norm.replace(".", "")
        if len(digits) >= 2 and norm not in out:   # ≥2 significant digits
            out.append(norm)
    return out


def check_attribution(answer: str, context: str) -> AttributionReport:
    """Verify *answer*'s salient facts trace to *context*. A number in the answer is
    supported iff its digit form appears in the context; an entity iff it (normalized)
    appears. Refusals and empty answers are trivially supported (nothing asserted)."""
    ans = (answer or "").strip()
    ctx = context or ""
    low_ans = ans.casefold()
    if not ans or low_ans.startswith(("i don't know", "i do not know")):
        return AttributionReport(True, [], [], 1.0, 1.0)

    ctx_nums = {_norm_num(m.group(0)) for m in _NUM.finditer(ctx)}
    ctx_nums |= {n.replace(".", "") for n in ctx_nums}     # also match without the dot
    nums = _checkable_numbers(ans)
    unsupported_nums = [n for n in nums
                        if n not in ctx_nums and n.replace(".", "") not in ctx_nums]

    ctx_ents = entities.normalized_set(ctx)
    ents = [e for e in entities.extract(ans) if entities.normalize(e) not in _ALWAYS_KNOWN]
    unsupported_ents = [e for e in ents if entities.normalize(e) not in ctx_ents]

    num_cov = 1.0 if not nums else round(1 - len(unsupported_nums) / len(nums), 4)
    ent_cov = 1.0 if not ents else round(1 - len(unsupported_ents) / len(ents), 4)
    return AttributionReport(
        supported=not unsupported_nums,           # the HARD gate is numeric fabrication
        unsupported_numbers=unsupported_nums,
        unsupported_entities=unsupported_ents,
        number_coverage=num_cov, entity_coverage=ent_cov,
    )


def vet_answer(answer: str, context: str, *, strict: bool = False,
               refusal: str = "I don't know.") -> tuple[str, AttributionReport]:
    """Return ``(answer_or_refusal, report)``. In ``strict`` mode an answer with an
    unsupported numeric claim is DOWNGRADED to *refusal* (structural no-fab). Advisory mode
    returns the answer unchanged with the report attached for logging/scoring."""
    report = check_attribution(answer, context)
    if strict and not report.supported:
        return refusal, report
    return answer, report


__all__ = ["AttributionReport", "check_attribution", "vet_answer"]
