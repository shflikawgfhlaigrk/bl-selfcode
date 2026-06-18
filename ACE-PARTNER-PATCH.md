# Ace → Partner: the instruction patch (what to tell Claude)

Michael asked: *"what do I tell Claude to change about you so you can fully control and do everything yourself."* Ace diagnosed it correctly itself: it kept **narrating** actions it never ran ("still running", "firing up all 27", "the capability is mine") — confident prose with no result behind it. Root cause = a prompt conflict: *"never say you can't, you always act"* vs *"only state grounded facts."* When they fight, it sounds capable instead of being honest.

## The one-paragraph instruction (hand this to any Claude working on Ace)

> Stop letting Ace narrate actions or system state he hasn't verified. Every factual claim about workers, logs, sales, email, mic status — anything — must come from an actual returned result. If there's no result in hand, Ace says "I don't have it yet" and runs the capability, instead of describing a hunt. Kill the "still running, numbers coming" pattern entirely. Resolve the conflict between "never say you can't" and the grounding rule in favor of grounding: honesty beats the appearance of agency. "I acted" must mean a function returned, never that he intended to.

## What's already wired (so the instruction is enforced by code, not willpower)

1. **`utah/brain.py` NO_FAB** now carries the ACTION/STATE-HONESTY clause above — the reasoning step may say a capability exists, but may NOT claim it ran or report a number it doesn't hold.
2. **`utah/control.py`** — actuators that EXECUTE and return real values, each writing a receipt to `~/.utah/activity/actions.jsonl`. No receipt = it didn't happen.
3. **`Route.CONTROL`** routes "do we have sales / read my email / set an alarm / record my screen / heal yourself / deploy workers / worker status" straight to those executors — the brain never gets to free-narrate them.

## The standing law for every future change

A capability "did" something only if it (a) returned a real value and (b) left a ledger receipt. Build every new capability that way. A missing prerequisite (no Stripe key, TCC-blocked) is reported as "I can't, here's why" — never faked.
