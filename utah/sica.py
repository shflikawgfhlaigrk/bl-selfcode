"""SICA governance for self-coding — utility score + versioned archive.

The measurable core of "A Self-Improving Coding Agent" (arXiv 2504.15228),
applied to Utah's self-coder. Today's gate is binary (suite green/red); SICA
adds a graded **utility** per attempt and an append-only **archive**, so
improvements compound and the meta-agent (next build) can pick the best-so-far
by ``argmax utility``. The exact paper utility:

    U = 0.5*score + 0.25*(1 - min(1, cost/$10)) + 0.25*(1 - min(1, time/300s))
    timeout  ->  U *= 0.5   (τ penalty)

Utah is CLI-subscription (no per-token cost), so ``cost_usd`` defaults to 0 (the
cost term is 1.0 = free) and efficiency is driven by wall-time; the term is kept
so a future metered lane scores correctly. This module is pure + I/O-isolated to
one JSONL file, so it is fully unit-proven.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass

from utah.daemon import runtime

#: SICA hard per-run limits (mirror selfcode.CODE_TIMEOUT_S = 300).
TIME_LIMIT_S = 300.0
COST_LIMIT_USD = 10.0
#: τ — a timed-out run's utility is halved (paper §overseer).
TIMEOUT_PENALTY = 0.5

#: Append-only archive of every scored self-code attempt.
ARCHIVE_PATH = runtime.RUN_DIR / "selfcode-archive.jsonl"


def utility(score: float, cost_usd: float = 0.0, elapsed_s: float = 0.0,
            timed_out: bool = False) -> float:
    """The SICA utility: accuracy + cost-efficiency + latency-efficiency."""
    score = max(0.0, min(1.0, float(score)))
    cost_term = 1.0 - min(1.0, max(0.0, float(cost_usd)) / COST_LIMIT_USD)
    time_term = 1.0 - min(1.0, max(0.0, float(elapsed_s)) / TIME_LIMIT_S)
    u = 0.5 * score + 0.25 * cost_term + 0.25 * time_term
    if timed_out:
        u *= TIMEOUT_PENALTY
    return round(u, 6)


def score_from_pytest(output: str) -> float:
    """Graded pass-rate from a ``pytest -q`` summary. All green -> 1.0; any
    fail/error -> passed/(passed+failed+errors); no recognizable summary -> 0.0."""
    text = output or ""

    def n(pattern: str) -> int:
        m = re.search(rf"(\d+) {pattern}", text)
        return int(m.group(1)) if m else 0

    passed, failed, errors = n("passed"), n("failed"), n("errors?")
    total = passed + failed + errors
    if total == 0:
        return 0.0
    if failed == 0 and errors == 0:
        return 1.0
    return round(passed / total, 6)


@dataclass
class Attempt:
    ts: float
    task: str
    branch: str | None
    tier: str | None
    passed: bool
    score: float
    cost_usd: float
    elapsed_s: float
    timed_out: bool
    utility: float
    merged: bool
    sha: str | None
    reason: str


def make_attempt(*, task: str, branch, tier, passed: bool, output: str,
                 cost_usd: float, elapsed_s: float, timed_out: bool,
                 merged: bool, sha, reason: str) -> Attempt:
    """Build a scored Attempt. A passing run scores 1.0; a failing run is graded
    by its pytest pass-rate so 'almost green' beats 'all red'."""
    score = 1.0 if passed else score_from_pytest(output)
    return Attempt(
        ts=time.time(), task=(task or "")[:200], branch=branch, tier=tier,
        passed=bool(passed), score=score, cost_usd=round(float(cost_usd), 4),
        elapsed_s=round(float(elapsed_s), 2), timed_out=bool(timed_out),
        utility=utility(score, cost_usd, elapsed_s, timed_out),
        merged=bool(merged), sha=sha, reason=(reason or "")[:200],
    )


class Archive:
    """Append-only JSONL archive of scored attempts (one line per attempt)."""

    def __init__(self, path=ARCHIVE_PATH):
        self.path = path

    def record(self, attempt) -> dict:
        d = attempt if isinstance(attempt, dict) else asdict(attempt)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(d) + "\n")
        return d

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        out: list[dict] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def best(self) -> dict | None:
        """The highest-utility attempt so far (the meta-agent's base) — None if empty."""
        e = self.entries()
        return max(e, key=lambda x: x.get("utility", 0.0)) if e else None

    def count(self) -> int:
        return len(self.entries())


__all__ = ["utility", "score_from_pytest", "Attempt", "make_attempt", "Archive",
           "ARCHIVE_PATH", "TIME_LIMIT_S", "COST_LIMIT_USD", "TIMEOUT_PENALTY"]
