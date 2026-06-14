"""Build a complete ProjectUtah per-file grade index for Obsidian tagging.

Merges existing RUBRIC-V2 rows from Desktop/grades/v2 (utah-core, utah-tests) with
v1 letter grades from sections/08-projectutah.md, then heuristically scores any file
still missing a row (grep-based caps from RUBRIC-V2-STRICT.md — conservative, not a
human re-read).

Outputs:
  - ops/utah-file-grades.json          (machine index, in repo)
  - ~/Desktop/grades/v2/utah-complete-index.md  (Obsidian hub with #grade/X tags)

Re-run after code or rubric changes:
  python3 ops/utah_grade_index.py
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRADES = Path.home() / "Desktop" / "grades"
OUT_JSON = ROOT / "ops" / "utah-file-grades.json"
OUT_MD = GRADES / "v2" / "utah-complete-index.md"

# Same scope as utah-core v2 grade (production + tests + ops + migrations + app shell).
SCAN_ROOTS = ("utah", "tests", "ops", "migrations", "app")
SCAN_SUFFIXES = {".py", ".html", ".sh", ".plist", ".dart"}
SKIP_PARTS = {"__pycache__", ".dart_tool", "build", "node_modules"}
GENERATED_MARKERS = ("dash/", "dashboard/", "index-BOPbo4Vv.js")

# RUBRIC-V2-STRICT hard caps (max score when condition matches).
CAP_RULES: list[tuple[str, int, str]] = [
    (r"sk-[a-zA-Z0-9]{20,}|api_key\s*=\s*['\"]|password\s*=\s*['\"][^'\"]+['\"]", 15, "secret"),
    (r"shell\s*=\s*True", 40, "shell_true"),
    (r"\beval\s*\(|\bexec\s*\(", 40, "eval_exec"),
    (r"/Users/[a-zA-Z0-9_./-]+", 62, "hardcoded_path"),
    (r"except\s*:\s*$|except\s+Exception\s*:\s*pass", 58, "swallowed_exception"),
]

# Paths that are off-limits / safety — always document if touched.
SAFETY_PATHS = {
    "utah/selfcode.py", "utah/config.py", "utah/brain.py",
    "utah/daemon/peercred.py", "utah/daemon/lifecycle.py",
    "utah/daemon/governor.py", "tests/conftest.py",
}


@dataclass
class FileGrade:
    path: str
    score: int | None
    letter: str
    cap: str | None
    failure: str
    source: str  # v2-core | v2-tests | v1 | heuristic | generated
    tag: str
    dims: str | None = None


def letter_from_score(score: int | None) -> str:
    if score is None:
        return "?"
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def tag_for_letter(letter: str) -> str:
    return f"#grade/{letter}" if letter != "?" else "#grade/ungraded"


def rel_path(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def discover_files() -> list[str]:
    out: list[str] = []
    for root_name in SCAN_ROOTS:
        base = ROOT / root_name
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.suffix not in SCAN_SUFFIXES:
                continue
            if any(part in SKIP_PARTS for part in p.parts):
                continue
            rp = rel_path(p)
            if any(m in rp for m in GENERATED_MARKERS):
                continue
            out.append(rp)
    return out


def parse_v2_table(text: str) -> dict[str, tuple[int, str | None, str, str | None]]:
    """path -> (score, cap, note, dims)"""
    found: dict[str, tuple[int, str | None, str, str | None]] = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        parts = [c.strip() for c in line.split("|")]
        if len(parts) < 6:
            continue
        path = parts[1]
        if not re.search(r"\.\w+$", path):
            continue
        score_m = re.search(r"\*?(\d+)\*?", parts[2])
        if not score_m:
            continue
        cap = parts[4] if parts[4] not in ("—", "-", "none", "") else None
        note = parts[5] if len(parts) > 5 else ""
        dims = parts[3] if len(parts) > 3 else None
        # normalize path (grades sometimes omit leading dirs)
        if not path.startswith(("utah/", "tests/", "ops/", "migrations/", "app/")):
            continue
        found[path] = (int(score_m.group(1)), cap, note, dims)
    return found


def parse_v1_table(text: str) -> dict[str, tuple[str, str]]:
    """path -> (letter, note)"""
    found: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        parts = [c.strip() for c in line.split("|")]
        if len(parts) < 4:
            continue
        path, grade = parts[1], parts[2]
        if grade not in ("A", "B", "C", "D", "F"):
            continue
        if not re.search(r"\.\w+$", path):
            continue
        note = parts[3] if len(parts) > 3 else ""
        found[path] = (grade, note)
    return found


def v1_to_score(letter: str) -> int:
    return {"A": 92, "B": 82, "C": 68, "D": 52, "F": 25}.get(letter, 60)


def heuristic_grade(path: str) -> FileGrade:
    full = ROOT / path
    if any(m in path for m in GENERATED_MARKERS):
        return FileGrade(path, 75, "B", "GENERATED", "Vendored/generated bundle",
                         "generated", tag_for_letter("B"), "75/75/75/75/75/75")

    if path in SAFETY_PATHS:
        return FileGrade(path, 85, "B", None,
                         "Tier-D safety path — human review required before edits",
                         "heuristic", tag_for_letter("B"))

    try:
        text = full.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return FileGrade(path, None, "?", None, f"unreadable: {exc}", "heuristic",
                         tag_for_letter("?"))

    # Test files: if no real assert patterns, cap 35
    if path.startswith("tests/") and path.endswith(".py"):
        asserts = len(re.findall(r"\bassert\b", text))
        if asserts < 2 and "pytest" not in text and "def test_" in text:
            return FileGrade(path, 35, "F", "fake_test",
                             "Few/no assertions — verify manually", "heuristic",
                             tag_for_letter("F"))

    cap_score = 100
    cap_name: str | None = None
    cap_evidence: list[str] = []
    path_cap_targets = path.endswith((".py", ".dart"))
    for pattern, cap, name in CAP_RULES:
        if name == "hardcoded_path" and not path_cap_targets:
            continue
        if re.search(pattern, text, re.M):
            if cap < cap_score:
                cap_score = cap
                cap_name = name
            cap_evidence.append(name)

    lines = text.count("\n") + 1
    if lines > 650:
        cap_score = min(cap_score, 68)
        cap_evidence.append("oversize_650")

    # subprocess / urlopen without timeout (live code only, not tests)
    if not path.startswith("tests/"):
        for m in re.finditer(r"(subprocess\.run|urlopen|Popen)\([^)]*\)", text):
            chunk = m.group(0)
            if "timeout" not in chunk:
                cap_score = min(cap_score, 68)
                cap_evidence.append("no_timeout")
                break

    # Conservative base before caps
    base = 72
    if path.startswith("tests/"):
        base = 80
    score = min(base, cap_score)
    letter = letter_from_score(score)
    failure = "; ".join(dict.fromkeys(cap_evidence)) if cap_evidence else "heuristic pass — no auto caps hit"
    if score >= 75 and not cap_evidence:
        failure = "No automated hazards; not a human v2 review"

    return FileGrade(path, score, letter, cap_name, failure, "heuristic",
                     tag_for_letter(letter))


def load_existing() -> tuple[dict, dict]:
    v2: dict[str, tuple[int, str | None, str, str | None]] = {}
    for name in ("utah-core.md", "utah-tests.md"):
        p = GRADES / "v2" / name
        if p.exists():
            v2.update(parse_v2_table(p.read_text(encoding="utf-8")))
    v1: dict[str, tuple[str, str]] = {}
    p = GRADES / "sections" / "08-projectutah.md"
    if p.exists():
        v1 = parse_v1_table(p.read_text(encoding="utf-8"))
    return v2, v1


def build_index() -> list[FileGrade]:
    v2, v1 = load_existing()
    files = discover_files()
    rows: list[FileGrade] = []

    for path in files:
        if path in v2:
            score, cap, note, dims = v2[path]
            letter = letter_from_score(score)
            src = "v2-tests" if path.startswith("tests/") else "v2-core"
            failure = note or (cap or "v2 graded")
            rows.append(FileGrade(path, score, letter, cap, failure, src,
                                  tag_for_letter(letter), dims))
            continue
        if path in v1:
            letter, note = v1[path]
            score = v1_to_score(letter)
            rows.append(FileGrade(path, score, letter, None, note or "v1 letter grade",
                                  "v1", tag_for_letter(letter)))
            continue
        rows.append(heuristic_grade(path))

    return sorted(rows, key=lambda r: (r.score or 0, r.path))


def write_json(rows: list[FileGrade]) -> None:
    payload = {
        "generated": date.today().isoformat(),
        "repo": str(ROOT),
        "rubric": "RUBRIC-V2-STRICT + v1 fallback + heuristic caps",
        "counts": {
            "total": len(rows),
            "by_letter": {g: sum(1 for r in rows if r.letter == g)
                          for g in ("A", "B", "C", "D", "F", "?")},
            "below_60": sum(1 for r in rows if r.score is not None and r.score < 60),
            "with_cap": sum(1 for r in rows if r.cap),
            "heuristic_only": sum(1 for r in rows if r.source == "heuristic"),
        },
        "files": [asdict(r) for r in rows],
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_markdown(rows: list[FileGrade]) -> None:
    GRADES.mkdir(parents=True, exist_ok=True)
    (GRADES / "v2").mkdir(parents=True, exist_ok=True)

    by_letter: dict[str, list[FileGrade]] = {g: [] for g in ("F", "D", "C", "B", "A", "?")}
    for r in rows:
        by_letter.setdefault(r.letter, []).append(r)

    failures = [r for r in rows if r.score is not None and r.score < 60 or r.cap]
    failures.sort(key=lambda r: (r.score or 0, r.path))

    lines = [
        "# ProjectUtah — complete file grade index",
        "",
        f"**Generated:** {date.today().isoformat()}  ",
        "**Vault tags:** `#grade/A` `#grade/B` `#grade/C` `#grade/D` `#grade/F` `#grade/ungraded`",
        "",
        "Re-run: `python3 ops/utah_grade_index.py` from ProjectUtah.",
        "",
        f"- **{len(rows)}** files indexed (`utah/`, `tests/`, `ops/`, `migrations/`, `app/`)",
        f"- **{sum(1 for r in rows if r.source.startswith('v2'))}** from human v2 pass",
        f"- **{sum(1 for r in rows if r.source == 'heuristic')}** heuristic-only (auto caps — verify)",
        f"- **{len(failures)}** rows with score &lt; 60 or a documented cap",
        "",
        "Tags on this note: #projectutah #grades #grade-index",
        "",
        "---",
        "",
        "## Failures & caps (tag these paths)",
        "",
        "Copy the **tag** column into Obsidian file notes or graph filters.",
        "",
        "| file | score | grade | cap | tag | failure |",
        "|------|-------|-------|-----|-----|---------|",
    ]

    for r in failures:
        gspan = f"<span class=\"g-{r.letter}\">{r.letter}</span>"
        cap = r.cap or "—"
        fail = (r.failure or "").replace("|", "/")[:120]
        lines.append(f"| `{r.path}` | {r.score or '—'} | {gspan} | {cap} | {r.tag} | {fail} |")

    lines += ["", "---", "", "## All files by letter grade", ""]

    order = ("F", "D", "C", "B", "A", "?")
    labels = {
        "A": "90–100 · ship quality",
        "B": "75–89 · solid",
        "C": "60–74 · concerns",
        "D": "40–59 · hazard",
        "F": "0–39 · broken / dishonest",
        "?": "ungraded / unreadable",
    }
    for letter in order:
        group = by_letter.get(letter) or []
        if not group:
            continue
        lines.append(f"### Grade {letter} — {labels.get(letter, '')} `{tag_for_letter(letter)}`")
        lines.append("")
        for r in group:
            src = f" _({r.source})_" if r.source == "heuristic" else ""
            cap = f" · cap {r.cap}" if r.cap else ""
            lines.append(f"- `{r.path}` · **{r.score}** {r.tag}{src}{cap}")
        lines.append("")

    lines += [
        "---",
        "",
        "## Obsidian quick filter",
        "",
        "- All failures: `tag:#grade/D OR tag:#grade/F`",
        "- Needs human review: paths with `heuristic` source in JSON",
        "- Machine index: `ProjectUtah/ops/utah-file-grades.json`",
        "",
    ]

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def update_grade_queue(rows: list[FileGrade]) -> None:
    """Refresh ops/grade-queue.json with open failures (score < 60, not fake_test noise)."""
    queue_path = ROOT / "ops" / "grade-queue.json"
    existing = []
    if queue_path.exists():
        try:
            existing = json.loads(queue_path.read_text())
        except json.JSONDecodeError:
            existing = []

    seen = {e.get("file") for e in existing if isinstance(e, dict)}
    new_entries = []
    for r in rows:
        if r.score is None or r.score >= 60:
            continue
        if r.path in seen:
            continue
        if r.cap == "fake_test" and r.score == 35:
            continue  # too noisy for queue
        new_entries.append({
            "file": r.path,
            "weakness": (r.failure or "score below 60")[:240],
            "verified_at": date.today().isoformat(),
            "source": f"utah_grade_index ({r.source}, score={r.score}, cap={r.cap})",
        })

    if new_entries:
        merged = existing + new_entries
        queue_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    rows = build_index()
    write_json(rows)
    write_markdown(rows)
    update_grade_queue(rows)
    print(f"Indexed {len(rows)} files")
    print(f"  JSON: {OUT_JSON}")
    print(f"  Obsidian: {OUT_MD}")
    below = sum(1 for r in rows if r.score is not None and r.score < 60)
    print(f"  Below 60: {below} · heuristic-only: {sum(1 for r in rows if r.source == 'heuristic')}")


if __name__ == "__main__":
    main()
