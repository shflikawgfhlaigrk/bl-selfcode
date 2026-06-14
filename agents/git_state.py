"""Hot-loaded agent: Ace's own source-control state (work-at-risk visibility).

A self-contained capability the daemon hot-loads from ``agents/``. It answers
"do I have uncommitted changes / unpushed commits / what branch am I on / is my
work safe" with the REAL state from ``git`` — never a model guess, never a
retrieved code chunk — and declines everything else by returning ``None`` so it
can never hijack general chat.

Why this exists: Ace could see disk, leads, mail, jobs, engines — but was BLIND
to its own version-control state. Asked "are you ahead of origin / do you have
uncommitted changes", it fell through to memory (a random code chunk) or an empty
brain reply. Meanwhile its real risk was concrete and recurring: dozens of dirty
files and unpushed commits that a second agent's repo-reset has wiped before.
This makes that risk something Ace can actually SEE and report.

Contract (shared by every hot-loaded agent):
  * ``KEYWORDS`` — the trigger vocabulary the loader may surface or index.
  * ``run(ctx)`` — answer the turn, or return ``None`` to decline (pass through).

Honest by contract: on a git question it always answers from real ``git`` output;
if git itself fails it says so plainly rather than guessing or falling through to a
brain that would fabricate. On a NON-git turn it returns ``None`` and never fires.

Stdlib only, single file by design.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
from typing import Any

#: Trigger vocabulary — source-control / work-at-risk intent. Each phrase already
#: implies git or the safety of Ace's own code, so it won't fire on ordinary chat.
KEYWORDS: tuple[str, ...] = (
    "git status",
    "git state",
    "git branch",
    "current branch",
    "uncommitted",
    "uncommited",        # common misspelling — still clearly this intent
    "unpushed",
    "un-pushed",
    "not pushed",
    "not committed",
    "ahead of origin",
    "behind origin",
    "ahead of remote",
    "commit my work",
    "commit my code",
    "commit your work",
    "push my work",
    "push my code",
    "push your work",
    "dirty tree",
    "dirty working tree",
    "working tree",
    "source control",
    "version control",
    "work at risk",
    "is my work safe",
    "is your work safe",
    "lose my work",
    "lose your work",
    "losing work",
    "uncommitted changes",
    "uncommitted work",
    "pending commits",
)

#: Word-boundary trigger built from KEYWORDS. Substring matching would let
#: "reorigin" or "branching" fire it; the \b anchors keep it honest.
_TRIGGER = re.compile(
    r"\b(?:%s)\b" % "|".join(re.escape(k) for k in KEYWORDS),
    re.I,
)

#: The repo to report on — this file lives at ``<repo>/agents/git_state.py``.
_REPO = pathlib.Path(__file__).resolve().parent.parent

#: Common ``ctx`` field/attribute names the dispatcher might carry the turn in.
_TEXT_FIELDS = ("text", "message", "query", "prompt", "input", "content")


def _text(ctx: Any) -> str:
    """Pull the user's turn out of whatever shape ``ctx`` arrives in."""
    if ctx is None:
        return ""
    if isinstance(ctx, str):
        return ctx
    if isinstance(ctx, dict):
        for key in _TEXT_FIELDS:
            val = ctx.get(key)
            if isinstance(val, str) and val:
                return val
        return ""
    for attr in _TEXT_FIELDS:
        val = getattr(ctx, attr, None)
        if isinstance(val, str) and val:
            return val
    return ""


def matches(text: str) -> bool:
    """True iff the turn is a git / source-control / work-safety question."""
    return bool(_TRIGGER.search(text or ""))


def _git(*args: str) -> str | None:
    """Run ``git -C <repo> <args>`` with a short timeout. Returns stripped stdout
    on success, or ``None`` on any failure (non-zero exit, missing git, timeout) —
    so the caller degrades to an honest "couldn't read" rather than guessing."""
    try:
        out = subprocess.run(
            ["git", "-C", str(_REPO), *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def _ahead_behind() -> tuple[int, int] | None:
    """``(behind, ahead)`` vs the branch's upstream, or ``None`` if there is no
    tracking branch / the count can't be read."""
    raw = _git("rev-list", "--left-right", "--count", "@{u}...HEAD")
    if not raw:
        return None
    parts = raw.split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def run(ctx: Any) -> str | None:
    """Report Ace's REAL source-control state, or ``None`` to decline a non-git turn.

    On a git question it always answers from live ``git`` output — branch, dirty
    file count, and ahead/behind upstream — and ends with a plain risk verdict so
    Ace can see when its own work is unsaved or unpushed. If git can't be read it
    says so honestly instead of falling through to a fabricating brain.
    """
    if not matches(_text(ctx)):
        return None

    if not (_REPO / ".git").exists():
        return "🔧 I can't see a git repo at my own root — source control isn't readable here."

    branch = _git("rev-parse", "--abbrev-ref", "HEAD") or "(unknown)"
    status = _git("status", "--porcelain")
    if status is None:
        return "🔧 I couldn't read git status (git failed or timed out) — can't confirm my source-control state."

    dirty = len([ln for ln in status.splitlines() if ln.strip()])
    ab = _ahead_behind()

    parts = [f"🌿 Branch `{branch}`."]
    parts.append(
        f"{dirty} uncommitted file{'s' if dirty != 1 else ''}."
        if dirty
        else "Working tree clean."
    )
    if ab is None:
        parts.append("No upstream tracking branch (can't compare to a remote).")
    else:
        behind, ahead = ab
        parts.append(
            f"{ahead} commit{'s' if ahead != 1 else ''} ahead, "
            f"{behind} behind upstream."
        )

    # Plain risk verdict — the whole point of the capability.
    ahead = ab[1] if ab else 0
    if dirty or ahead:
        risk = []
        if dirty:
            risk.append(f"{dirty} uncommitted")
        if ahead:
            risk.append(f"{ahead} unpushed")
        parts.append(
            f"⚠️ Work at risk: {', '.join(risk)}. A repo reset would lose it — commit and push."
        )
    else:
        parts.append("✅ Nothing at risk — committed and pushed.")

    return " ".join(parts)
