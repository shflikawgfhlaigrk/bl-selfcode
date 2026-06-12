"""Advisory capability — Ace's coach/concierge/navigator/planner/lawdie/lawyer transition
here (NOT agents): brain-backed, grounded advice. Each "advisor" is a persona prompt routed
through the SAME grounded brain (recall→ground→reason→answer), so advice is grounded in
Utah's memory and never fabricated. Degrades to an honest message if the brain is down.
"""
from __future__ import annotations

import logging

log = logging.getLogger("utah.advisory")

#: persona -> framing. lawyer/lawdie advice is explicitly non-binding (not legal advice).
PERSONAS = {
    "coach": "You are a concise accountability coach.",
    "concierge": "You are a practical concierge who arranges and recommends.",
    "navigator": "You are a navigator who gives clear step-by-step directions of action.",
    "planner": "You are a planner who breaks goals into ordered, concrete steps.",
    "lawyer": "You are a careful paralegal. Give general information, not legal advice; "
              "recommend a licensed attorney for anything binding.",
}
PERSONAS["lawdie"] = PERSONAS["lawyer"]


def advise(persona: str | None, question: str | None, *, tell=None) -> dict:
    """Answer *question* in *persona* voice via the grounded brain. ``tell`` is injectable
    (defaults to core.tell). Returns ``{persona, answer, source}``; never raises.

    Honesty contract: the reported ``persona`` is the one that ACTUALLY framed the
    prompt (an unknown name resolves to — and is reported as — "coach"); a blank
    question never spends the brain lane (``source: invalid``); a dead/None reply is
    ``source: unavailable``; a blank reply degrades to "I don't know." — a dead lane
    is never dressed up as an answer."""
    requested = (persona or "coach").strip().lower()
    persona = requested if requested in PERSONAS else "coach"
    framing = PERSONAS[persona]
    question = (question or "").strip()
    if not question:
        return {"persona": persona, "answer": "(advisory needs a question)",
                "source": "invalid"}
    if tell is None:
        from utah import core
        tell = core.tell
    try:
        reply = tell(f"{framing}\n\n{question}")
    except Exception as exc:  # noqa: BLE001 — boundary fn: any brain failure degrades honestly
        log.warning("advisory %s failed: %s", persona, exc)
        return {"persona": persona, "answer": f"(advisory unavailable: {exc})",
                "source": "unavailable"}
    if reply is None:
        log.warning("advisory %s: tell returned None (lane dead)", persona)
        return {"persona": persona,
                "answer": "(advisory unavailable: the brain returned nothing)",
                "source": "unavailable"}
    text = str(getattr(reply, "text", reply)).strip()
    return {"persona": persona,
            "answer": text or "I don't know.",
            "source": getattr(getattr(reply, "source", None), "value", "brain")}


__all__ = ["advise", "PERSONAS"]
