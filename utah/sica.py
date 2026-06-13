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
import os
import re
import time
from dataclasses import asdict, dataclass

from utah.daemon import runtime

#: SICA hard per-run limits. Env-overridable per deploy (still HARD bounds — the overseer
#: kills past TIME_LIMIT_S and the utility time-term normalizes against it): a bot editing
#: a large file + running the full gate needs more than the 300s default, so the autonomous
#: launchd job raises it via UTAH_SELFCODE_TIME_LIMIT. Default 300s keeps every other caller
#: (and the unit tests, which pass explicit values) unchanged.
TIME_LIMIT_S = float(os.environ.get("UTAH_SELFCODE_TIME_LIMIT", "300"))
COST_LIMIT_USD = float(os.environ.get("UTAH_SELFCODE_COST_LIMIT", "10"))
#: τ — a timed-out run's utility is halved (paper §overseer).
TIMEOUT_PENALTY = 0.5
#: Repeat-discount factor. The raw utility (pass-rate + cheap + fast) is argmax-ed by a
#: trivial repeated task (audit 2026-06-12: the loop merged the same `_probe_marker.py`
#: test 9 times because every repeat scored ~max utility). At READ time, a task whose
#: text near-duplicates n-1 other archived attempts has ALL its entries discounted by
#: REPEAT_PENALTY**(n-1), so spam decays toward zero while once-done work keeps its full
#: score. Read-time (not stored) so history is fixed without rewriting the archive.
REPEAT_PENALTY = float(os.environ.get("UTAH_SELFCODE_REPEAT_PENALTY", "0.2"))
#: Task subjects that can never be "best so far" nor a generated task's target.
#: `_probe_marker.py` is the diff-capture probe's inert landing file; the loop
#: reward-hacked it (its original u=1.0 archive entry seeded 9 spam merges).
#: "utah probe"/"probe marker" are observed re-wordings of the same subject, and
#: `utah/_meta*` is the ORIGINAL archive-mimicry family (trivial _meta files) that
#: sica_goals was built to replace — none of these may anchor the meta-prompt.
#: sica_goals re-exports this as BANNED_TASK_TARGETS for its generation gate.
BANNED_TASK_SUBSTRINGS: tuple[str, ...] = ("_probe_marker", "probe marker",
                                           "utah probe", "utah/_meta")

#: Legacy file path for the archive — retained for isolated tests (``Archive(path=…)``)
#: and one-time migration of pre-existing entries. The PRODUCTION store is Postgres
#: (everything durable lives in PG); see :class:`_PgArchive`.
ARCHIVE_PATH = runtime.RUN_DIR / "selfcode-archive.jsonl"

#: Postgres schema for the self-code archive (created lazily on first use).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS selfcode_archive (
  id bigserial PRIMARY KEY,
  utility double precision NOT NULL DEFAULT 0,
  data jsonb NOT NULL,
  ts timestamptz NOT NULL DEFAULT now()
);
"""


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


def normalize_task(text: str) -> frozenset[str]:
    """Order/punctuation-insensitive token fingerprint of a task sentence."""
    return frozenset(t for t in re.findall(r"[a-z0-9_./]+", (text or "").lower())
                     if len(t) > 2)


def task_similar(a: str, b: str, threshold: float = 0.6) -> bool:
    """Near-duplicate check: Jaccard overlap of token fingerprints. Catches the
    observed degeneracy (same task re-worded: 'exposes a module-level constant' vs
    'defines a module-level constant') without flagging genuinely different work."""
    ta, tb = normalize_task(a), normalize_task(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


def effective_entries(entries: list[dict]) -> list[dict]:
    """Shallow copies with repeat-discounted utility: each entry's utility is
    multiplied by ``REPEAT_PENALTY ** (number of OTHER near-duplicate entries)``.
    A task done once is untouched; a task spammed n times decays toward zero, so
    ``argmax utility`` (the meta-agent's "best so far") stops showcasing spam."""
    out = []
    tasks = [str(e.get("task", "")) for e in entries]
    for i, e in enumerate(entries):
        dups = sum(1 for j, t in enumerate(tasks) if j != i and task_similar(tasks[i], t))
        d = dict(e)
        if dups:
            d["utility"] = round(float(d.get("utility", 0.0) or 0.0)
                                 * (REPEAT_PENALTY ** dups), 6)
            d["repeat_discounted"] = dups + 1
        out.append(d)
    return out


def best_effective(entries: list[dict]) -> dict | None:
    """The highest *repeat-discounted* utility attempt — what the meta-agent should
    treat as best-so-far. Banned-subject tasks are excluded outright: the original
    ``_probe_marker`` entry was done ONCE (so the repeat discount never touched it)
    yet showcasing its u=1.0 as "best" is what seeded the spam family. ``None`` if
    nothing eligible."""
    eff = [e for e in effective_entries(entries or [])
           if not any(b in str(e.get("task", "")).lower() for b in BANNED_TASK_SUBSTRINGS)]
    # Newest among equals: utility saturates at 1.0 for any small green change, so an
    # insertion-order tie-break showcased the oldest trivial entry forever.
    return max(eff, key=lambda x: (float(x.get("utility", 0.0) or 0.0),
                                   float(x.get("ts", 0.0) or 0.0))) if eff else None


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


class _FileArchive:
    """JSONL file backend — isolated tests (``Archive(path=…)``) and migration source."""

    def __init__(self, path=ARCHIVE_PATH):
        self.path = path

    def record(self, d: dict) -> dict:
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


class _MemArchive:
    """In-memory backend — the unit-test default (pinned in conftest, like FakeStore)."""

    def __init__(self) -> None:
        self._rows: list[dict] = []

    def record(self, d: dict) -> dict:
        self._rows.append(dict(d))
        return d

    def entries(self) -> list[dict]:
        return [dict(r) for r in self._rows]


class _PgArchive:
    """Postgres backend — the PRODUCTION store. Append-only ``selfcode_archive`` table;
    the schema is created lazily on first use so no boot wiring is required."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn
        self._ready = False

    def _conn(self):
        import psycopg
        from utah import config
        # Bounded both ways: connect_timeout caps a dead host; statement_timeout caps a
        # wedged query (a stuck archive write would otherwise hang the autonomy cycle).
        return psycopg.connect(
            self._dsn or config.DB_DSN, autocommit=True, connect_timeout=8,
            options=f"-c statement_timeout={config.DB_STATEMENT_TIMEOUT_MS}")

    def _ensure(self, c) -> None:
        if not self._ready:
            c.execute(_SCHEMA)
            self._ready = True

    def record(self, d: dict) -> dict:
        with self._conn() as c:
            self._ensure(c)
            c.execute("INSERT INTO selfcode_archive (utility, data) VALUES (%s, %s)",
                      (float(d.get("utility", 0.0) or 0.0), json.dumps(d)))
        return d

    def entries(self) -> list[dict]:
        with self._conn() as c:
            self._ensure(c)
            rows = c.execute("SELECT data FROM selfcode_archive ORDER BY id").fetchall()
        return [r[0] if isinstance(r[0], dict) else json.loads(r[0]) for r in rows]


_backend = None


def get_archive_backend():
    """The process-wide archive backend (default: Postgres). Tests pin :class:`_MemArchive`."""
    global _backend
    if _backend is None:
        _backend = _PgArchive()
    return _backend


def set_archive_backend(backend) -> None:
    """Inject the archive backend (tests). ``None`` restores the Postgres default."""
    global _backend
    _backend = backend


#: Per-cycle telemetry table (append-only) — also Postgres, not a file.
_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS selfcode_log (
  id bigserial PRIMARY KEY,
  ts timestamptz NOT NULL DEFAULT now(),
  data jsonb NOT NULL
);
"""


def record_cycle(d: dict) -> None:
    """Append one self-code cycle's telemetry to Postgres (best-effort; never raises).
    A no-op unless the archive backend is Postgres, so unit tests never hit a real DB
    (the conftest pin makes the backend in-memory)."""
    backend = get_archive_backend()
    if not isinstance(backend, _PgArchive):
        return
    try:
        with backend._conn() as c:
            c.execute(_LOG_SCHEMA)
            c.execute("INSERT INTO selfcode_log (data) VALUES (%s)", (json.dumps(d),))
    except Exception:  # noqa: BLE001 — telemetry is best-effort
        pass


def recent_cycles(limit: int = 80) -> list[dict]:
    """Recent self-coding cycle telemetry, NEWEST FIRST, from ``selfcode_log``. The mirror of
    :func:`record_cycle`: a no-op (``[]``) unless the archive backend is Postgres, so unit tests
    (in-memory backend, pinned in conftest) never touch a DB. Best-effort — any DB error → ``[]``;
    never raises (a dead telemetry log must not break the loop that reads it)."""
    backend = get_archive_backend()
    if not isinstance(backend, _PgArchive):
        return []
    try:
        with backend._conn() as c:
            c.execute(_LOG_SCHEMA)
            rows = c.execute(
                "SELECT data FROM selfcode_log ORDER BY id DESC LIMIT %s", (limit,)
            ).fetchall()
        return [r[0] if isinstance(r[0], dict) else json.loads(r[0]) for r in rows]
    except Exception:  # noqa: BLE001 — telemetry read is best-effort
        return []


class Archive:
    """Scored-attempt archive. Production = Postgres; passing a ``path`` forces the file
    backend (isolated tests / migration). ``best()``/``count()`` are computed over
    ``entries()`` so every backend shares identical semantics."""

    def __init__(self, path=None, backend=None):
        if backend is not None:
            self._b = backend
        elif path is not None:
            self._b = _FileArchive(path)
        else:
            self._b = get_archive_backend()

    def record(self, attempt) -> dict:
        d = attempt if isinstance(attempt, dict) else asdict(attempt)
        return self._b.record(d)

    def entries(self) -> list[dict]:
        return self._b.entries()

    def best(self) -> dict | None:
        """The highest-utility attempt so far (the meta-agent's base) — None if empty."""
        e = self.entries()
        return max(e, key=lambda x: x.get("utility", 0.0)) if e else None

    def count(self) -> int:
        return len(self.entries())


__all__ = ["utility", "score_from_pytest", "Attempt", "make_attempt", "Archive",
           "get_archive_backend", "set_archive_backend", "record_cycle", "recent_cycles",
           "normalize_task", "task_similar", "effective_entries", "best_effective",
           "ARCHIVE_PATH", "TIME_LIMIT_S", "COST_LIMIT_USD", "TIMEOUT_PENALTY",
           "REPEAT_PENALTY", "BANNED_TASK_SUBSTRINGS"]
