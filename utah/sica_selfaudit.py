"""Self-audit — Ace CONSTANTLY finds things to fix about himself (Michael's directive,
2026-06-10). A daily sweep of the codebase + failure log that files concrete, bounded
repair tasks into the same dedup'd findings queue the self-code loop already prefers
over its rotation (a pending finding pre-empts the wheel). Three code lenses + the
existing failure lens:

  1. TODO/FIXME/XXX comments       → "resolve the TODO at <file>:<line>: <text>"
  2. modules with no test file     → "add a unit test covering <module>"   (Tier-A)
  3. files nearing the 700 ceiling → "split <file> (<n> lines)"

Everything is real (read from the live tree), bounded (caps per lens per run so the
queue never floods), and dedup'd by the queue itself (re-filing the same task is a
no-op). Pure scanners + injectable root — tested without touching the repo.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from utah import sica_discover

log = logging.getLogger("utah.sica_selfaudit")

_ROOT = Path(__file__).resolve().parent            # utah/ package dir
_MARK = re.compile(r"#\s*(TODO|FIXME|XXX)\b[:\s]*(.{0,90})")
_CAPS = {"todo": 3, "untested": 3, "oversize": 2}  # per-run flood guard
_SIZE_CEILING = 650                                 # propose a split BEFORE 700 breaks the rule
_SKIP_TESTS_FOR = {"__init__", "objects"}           # trivial/dataclass-only modules


def todo_findings(root: Path | None = None, cap: int = _CAPS["todo"]) -> list[str]:
    """Oldest-path-first TODO/FIXME/XXX markers as concrete tasks."""
    root = root or _ROOT
    out: list[str] = []
    for py in sorted(root.rglob("*.py")):
        if "test" in py.name or "__pycache__" in str(py):
            continue
        try:
            for n, line in enumerate(py.read_text(encoding="utf-8", errors="replace")
                                     .splitlines(), 1):
                m = _MARK.search(line)
                if m:
                    rel = py.relative_to(root.parent)
                    out.append(f"Resolve the {m.group(1)} at {rel}:{n} — "
                               f"{m.group(2).strip() or 'see comment'}")
                    if len(out) >= cap:
                        return out
        except OSError:
            continue
    return out


def untested_findings(root: Path | None = None, tests_dir: Path | None = None,
                      cap: int = _CAPS["untested"]) -> list[str]:
    """Modules with no tests/test_<stem>*.py — leaf-friendly Tier-A work the auto-merge
    ladder can land on its own."""
    root = root or _ROOT
    tests = tests_dir or (root.parent / "tests")
    have = {p.name for p in tests.glob("test_*.py")} if tests.exists() else set()
    out: list[str] = []
    for py in sorted(root.rglob("*.py")):
        stem = py.stem
        if (stem.startswith("_") or stem in _SKIP_TESTS_FOR or "test" in stem
                or "__pycache__" in str(py)):
            continue
        if not any(stem in t for t in have):
            rel = py.relative_to(root.parent)
            out.append(f"Add a focused unit test covering {rel} (no tests/test_{stem}*.py "
                       "exists — pick its purest function)")
            if len(out) >= cap:
                break
    return out


def oversize_findings(root: Path | None = None, cap: int = _CAPS["oversize"],
                      ceiling: int = _SIZE_CEILING) -> list[str]:
    """Files approaching the 700-line house ceiling — propose the split early."""
    root = root or _ROOT
    sized: list[tuple[int, Path]] = []
    for py in root.rglob("*.py"):
        if "test" in py.name or "__pycache__" in str(py):
            continue
        try:
            n = sum(1 for _ in py.open(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if n > ceiling:
            sized.append((n, py))
    sized.sort(reverse=True)
    return [f"Plan a split of {p.relative_to(root.parent)} ({n} lines — house ceiling "
            "is 700): propose the seam, don't move code yet"
            for n, p in sized[:cap]]


def run(*, root: Path | None = None, file_fn=None, harvest: bool = True) -> dict:
    """One audit sweep → file every finding into the queue (dedup makes re-runs free).
    Also triggers the existing failure-log harvest so ALL self-signals land together."""
    file_fn = file_fn or (lambda task: sica_discover.file_task("autonomy", task))
    found: list[str] = []
    found += todo_findings(root)
    found += untested_findings(root)
    found += oversize_findings(root)
    filed = 0
    for task in found:
        try:
            res = file_fn(task)
            if isinstance(res, dict) and res.get("filed"):
                filed += 1   # dedup'd repeats return filed=False — correct no-op
        except Exception as exc:  # noqa: BLE001 — one bad task never kills the sweep
            log.warning("self-audit file failed: %s", exc)
    harvested = 0
    if harvest:
        try:
            harvested = len(sica_discover.harvest_failure_findings() or [])
        except Exception as exc:  # noqa: BLE001
            log.warning("failure harvest failed: %s", exc)
    log.info("self-audit: %d code findings (%d filed new) + %d failure findings",
             len(found), filed, harvested)
    return {"found": len(found), "filed_new": filed, "failure_findings": harvested}


if __name__ == "__main__":   # pragma: no cover — the com.utah.selfaudit cron entry
    print(run())


__all__ = ["run", "todo_findings", "untested_findings", "oversize_findings"]
