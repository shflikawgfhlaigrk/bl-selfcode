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


def advise(persona: str, question: str, *, tell=None) -> dict:
    """Answer *question* in *persona* voice via the grounded brain. ``tell`` is injectable
    (defaults to core.tell). Returns ``{persona, answer, source}``; never raises."""
    persona = (persona or "coach").lower()
    framing = PERSONAS.get(persona, PERSONAS["coach"])
    if tell is None:
        from utah import core
        tell = core.tell
    try:
        reply = tell(f"{framing}\n\n{question}")
    except Exception as exc:  # noqa: BLE001
        log.warning("advisory %s failed: %s", persona, exc)
        return {"persona": persona, "answer": f"(advisory unavailable: {exc})", "source": "unavailable"}
    return {"persona": persona,
            "answer": getattr(reply, "text", str(reply)),
            "source": getattr(getattr(reply, "source", None), "value", "brain")}


__all__ = ["advise", "PERSONAS"]
