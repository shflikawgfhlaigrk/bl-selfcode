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

Nothing here runs autonomously yet — auto-merge stays OFF until rung 3 (async
overseer) lands. This is the mechanism the governed autonomous lane will call.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from utah import selfcode, sica

_META_PROMPT = (
    "You are the meta-agent improving Utah's OWN codebase (a self-improving coding "
    "agent). Below is the recent self-coding archive. Propose the SINGLE next "
    "concrete improvement task: one sentence, actionable, LOW-RISK (a leaf capability, "
    "a doc, or a helper — never touch safety files: selfcode/config/brain/peercred/"
    "lifecycle/governor). Reply with ONLY the task text.\n\n"
    "Best so far (utility {best_u}): {best_task}\n\n"
    "Recent attempts:\n{recent}\n\nNext task:"
)


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
        b = self.archive.best()
        return float(b.get("utility", 0.0)) if b else 0.0

    def next_task_from_archive(self, brain_fn) -> str:
        """Meta-agent: read the archive (best + recent) and ask the brain for the
        next improvement task. ``brain_fn(prompt) -> str`` is injected (the real
        one is utah.brain)."""
        best = self.archive.best() or {}
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
            out = self._propose(task) or {}
            res.steps += 1
            spent += float(out.get("cost_usd", 0.0) or 0.0)
            res.attempts.append({
                "task": task,
                "utility": float(out.get("utility", 0.0) or 0.0),
                "passed": bool(out.get("tests_passed")),
                "merged": bool(out.get("merged")),
            })
        res.best_after = self._best_u()
        res.improved = res.best_after > res.best_before
        res.stopped = stopped
        return res


__all__ = ["MetaLoop", "LoopResult"]
