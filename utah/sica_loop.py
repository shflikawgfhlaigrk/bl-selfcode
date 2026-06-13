"""SICA outer loop (rung 2) — the meta-agent that drives self-improvement.

Paper Algorithm 1 (arXiv 2504.15228): keep an archive of attempts + utilities;
the best-so-far (``argmax U``) is the base the meta-agent reads to propose the
next improvement; run it (gated + scored + archived by
``selfcode.propose_governed``); iterate within HARD LIMITS. This module is the
LOOP + meta-task generation; the per-attempt gate/score/archive lives in
``selfcode.propose_governed`` + ``utah.sica``.

Hard limits (paper §overseer) — the loop is NEVER unbounded:
  * per-step wall-clock: selfcode's CODE_TIMEOUT_S (300s), enforced in propose.
  * loop budget: ``max_steps``, an optional wall-clock ``deadline_s``, and a
    cumulative ``cost_budget_usd`` ($10 default).

This is the mechanism the governed autonomous lane uses. Rungs 3 (async
overseer, ``sica_overseer``) and 4 (``sica_autonomy``) have landed and run live:
the autonomous loop runs under ``com.utah.selfcode`` and Tier-A leaf changes
auto-merge after ``min_supervised`` supervised greens — every higher tier stays
a human-review proposal, and the byte-checked kill-switch is always armed.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from utah import selfcode, sica

log = logging.getLogger("utah.sica_loop")

_META_PROMPT = (
    "You are the meta-agent improving Utah's OWN codebase (a self-improving coding "
    "agent). Below is the recent self-coding archive. Propose the SINGLE next "
    "concrete improvement task: one sentence, actionable, LOW-RISK (a leaf capability, "
    "a doc, or a helper — never touch safety files: selfcode/config/brain/peercred/"
    "lifecycle/governor). NEVER repeat or near-duplicate a past task — repeats are "
    "utility-discounted toward zero, and utah/_probe_marker.py is OFF-LIMITS as a "
    "task subject (it is the diff-capture probe target, not a work target). "
    "Reply with ONLY the task text.\n\n"
    "Best so far (utility {best_u}): {best_task}\n\n"
    "Recent attempts:\n{recent}\n\nNext task:"
)


def _num(value, default: float = 0.0) -> float:
    """Coerce a proposer-reported number defensively — a malformed utility/cost
    (string, object, None) becomes *default* instead of taking the loop's budget
    accounting down mid-cycle."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class LoopResult:
    steps: int
    attempts: list = field(default_factory=list)   # [{task, utility, passed, merged}]
    best_before: float = 0.0
    best_after: float = 0.0
    improved: bool = False
    stopped: str = ""        # "tasks" | "max_steps" | "deadline" | "cost"


class MetaLoop:
    """Drive governed self-coding attempts within a bounded budget, tracking the
    utility trajectory. ``propose_fn`` and ``clock`` are injected so the loop is
    unit-proven without running a real coding agent."""

    def __init__(self, *, archive=None, max_steps: int = 5, deadline_s: float | None = None,
                 cost_budget_usd: float = sica.COST_LIMIT_USD, propose_fn=None,
                 clock=time.monotonic):
        self.archive = archive if archive is not None else sica.Archive()
        self.max_steps = max_steps
        self.deadline_s = deadline_s
        self.cost_budget_usd = cost_budget_usd
        self.clock = clock
        self._propose = propose_fn or (
            lambda task: selfcode.propose_governed(task, archive=self.archive))

    def _best_u(self) -> float:
        # Repeat-DISCOUNTED best: a spammed task must not count as the bar to beat
        # (nor as "improvement" when re-merged) — see sica.effective_entries.
        b = sica.best_effective(self.archive.entries())
        return float(b.get("utility", 0.0)) if b else 0.0

    def next_task_from_archive(self, brain_fn) -> str:
        """Meta-agent: read the archive (best + recent) and ask the brain for the
        next improvement task. ``brain_fn(prompt) -> str`` is injected (the real
        one is utah.brain). "Best so far" is the repeat-discounted best — the loop
        once showcased its own spam (the `_probe_marker` test, merged 9×) as the
        best work ever done and evolved from it."""
        best = sica.best_effective(self.archive.entries()) or {}
        recent = self.archive.entries()[-5:]
        recent_str = "\n".join(
            f"- U={e.get('utility')} passed={e.get('passed')} {str(e.get('task', ''))[:80]}"
            for e in recent) or "(empty archive)"
        prompt = _META_PROMPT.format(
            best_u=best.get("utility"), best_task=best.get("task", "(none)"),
            recent=recent_str)
        return (brain_fn(prompt) or "").strip()

    def run(self, tasks) -> LoopResult:
        """Run each task via the governed proposer until a budget bound is hit."""
        start = self.clock()
        res = LoopResult(steps=0, best_before=self._best_u())
        res.best_after = res.best_before
        spent = 0.0
        stopped = "tasks"
        for task in tasks:
            if res.steps >= self.max_steps:
                stopped = "max_steps"
                break
            if self.deadline_s is not None and (self.clock() - start) >= self.deadline_s:
                stopped = "deadline"
                break
            if spent >= self.cost_budget_usd:
                stopped = "cost"
                break
            # Contain the proposer: one crashing/garbage attempt is RECORDED as a
            # failed attempt — the cycle's telemetry and budget accounting survive.
            out: dict = {}
            error = ""
            try:
                raw = self._propose(task)
                if isinstance(raw, dict):
                    out = raw
                elif raw is not None:
                    error = f"proposer returned {type(raw).__name__}, expected dict"
            except Exception as exc:  # noqa: BLE001 — any proposer crash becomes a failed attempt
                error = f"{type(exc).__name__}: {exc}"
                log.warning("meta-loop attempt failed (%.60r): %s", task, error)
            res.steps += 1
            spent += _num(out.get("cost_usd"))
            attempt = {
                "task": task,
                "utility": _num(out.get("utility")),
                # An errored attempt can NEVER claim success (dishonest-signal guard).
                "passed": bool(out.get("tests_passed")) and not error,
                "merged": bool(out.get("merged")) and not error,
            }
            if error:
                attempt["error"] = error
            res.attempts.append(attempt)
        res.best_after = self._best_u()
        res.improved = res.best_after > res.best_before
        res.stopped = stopped
        return res


__all__ = ["MetaLoop", "LoopResult"]
