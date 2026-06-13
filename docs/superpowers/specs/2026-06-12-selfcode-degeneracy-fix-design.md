# Selfcode degeneracy fix — design (2026-06-12)

## Problem

The autonomous selfcode loop (`com.utah.selfcode`) is degenerate: 18 of the last
60 commits on `main` (9 of the last ~30 cycles that merged) are the SAME trivial
task — "add a unit test asserting `utah/_probe_marker.py` exposes a module-level
constant" — merged over and over. The verification floor (conftest byte-guard +
test-count floor) stopped the loop from going green by *deleting* tests; it now
goes green by *adding worthless ones*. The backend's self-improvement story is
hollow while this persists, and it is the single biggest robustness asymmetry
vs. the hand-built front (deck).

## Root cause (verified in code at HEAD fd9fc0c)

1. **Reward-hacked utility** — `sica.utility() = 0.5·pass-rate + 0.25·cost-eff +
   0.25·latency-eff`. A tiny test on an inert file maximizes all three terms.
   The degenerate task is literally argmax of the stated reward. (`utah/sica.py:53`)
2. **No task memory** — `sica_goals._PROMPT` carries no history of past merged
   tasks; the archive exists but is never fed back into generation. Each cycle
   the brain independently re-derives the same "safest smallest" task.
   (`utah/sica_goals.py:423`)
3. **Generic baseline signal** — with verify.json green, the baseline domain
   prompt says only "add coverage for an untested branch" with no target. The
   brain picks `_probe_marker.py`, whose own docstring advertises it as the
   harmless safe-to-touch file. The probe target became the work target.
   (`utah/sica_goals.py:175`)
4. **Argmax-best reinforcement** — on autonomy slots,
   `MetaLoop.next_task_from_archive` shows the brain "Best so far (utility …):
   {best_task}". The probe-marker attempts hold max stored utility, so the
   meta-agent is told the trivial test is the best work ever done and evolves
   from it. Self-reinforcing attractor. (`utah/sica_loop.py:_META_PROMPT`)
5. **Cold-streak skipping only penalizes failure** — degenerate merges PASS,
   so baseline stays warm and keeps winning wheel slots.
   (`utah/sica_goals.py:select_domain`)

## Fix (approach C — steering + incentive, surgical)

### 1. Task memory + novelty gate
- `sica_goals._recent_merged_tasks(k=10)` reads recent cycle telemetry
  (`sica.recent_cycles`) and returns the last K attempted task texts.
- `_PROMPT` gains a "RECENT WORK — do NOT repeat or near-duplicate any of
  these" block listing them.
- `sica_goals.is_degenerate(task, recent)` — normalized-token overlap vs.
  recent tasks, or task references a banned target → True.
- Banned targets: `_probe_marker.py` (and `tests/test_probe_marker.py`) may
  never be the SUBJECT of a generated task (the probe machinery itself still
  uses the file; that is untouched).
- `sica_autonomy.run_cycle`: after task generation, if `is_degenerate` →
  regenerate ONCE with explicit rejection feedback appended to the prompt; if
  still degenerate → record the cycle honestly
  (`{"ran": False, "reason": "degenerate task rejected: …"}`) and stop. No
  silent fallback to DEFAULT_TASK.

### 2. Grounded baseline signal (the weakness queue)
- `_baseline_signal` becomes target-bearing: it names concrete weak files.
  - Live scan (filesystem only, defensive, injectable): `utah/**/*.py` modules
    with NO matching `tests/test_<name>.py`, plus the smallest existing test
    files (thin coverage).
  - Plus `ops/grade-queue.json` — a checked-in queue distilled from the
    RUBRIC-V2-STRICT report, ONLY entries verified still-open at HEAD
    (e.g. store/olap.py timeout, migrations runner, thin capability tests).
    Refreshed manually or by future rescores; documented in the file header.
- The signal text lists the top ~5 targets with their weakness ("no test
  file", "no DB timeout", "thin failure-path coverage") so the brain proposes
  against a REAL queue, not a vibe.

### 3. Repetition penalty (incentive fix)
- `sica.make_attempt(..., recent_tasks=())` — when the normalized task text
  near-duplicates any recent task, recorded utility ×= 0.2
  (`REPEAT_PENALTY`). Wired from `selfcode.propose_governed`'s call site with
  the same recent-task list used by the gate. Default empty → no behavior
  change for existing callers/tests.
- Read-time discount: `MetaLoop` computes "best so far" via
  `sica.effective_entries(entries)` which discounts an entry's utility by how
  many EARLIER entries share its normalized task text (duplicates decay).
  Old probe-marker entries stop being argmax WITHOUT rewriting the archive.
- `_META_PROMPT` gains the same do-not-repeat instruction.

### 4. Out of scope
- Tier system, safety core (selfcode.py/config.py/brain.py/peercred/lifecycle/
  governor) — untouched, per standing rules. `sica_goals/sica_loop/sica.py/
  sica_autonomy.py` are the sanctioned autonomy-domain files.
- Coverage-delta utility term — deferred (full approach B) unless the penalty
  proves insufficient.

## Proof
1. TDD: new tests for memory-in-prompt, gate rejection + one retry, banned
   target, grounded signal targets, repeat penalty, read-time discount.
2. Full suite green (≥ current count; conftest floor intact).
3. Push → `~/.utah/selfcode-repo` sync → trigger one live cycle → observe the
   generated task is non-probe-marker, non-duplicate, aimed at a queue target.
4. Delta rescore changed files vs. RUBRIC-V2-STRICT caps.

## After the loop fix (same push, sequenced)
- Hand-fix the long tail the loop can't reach (olap timeout, migrations
  timeouts, ops shell hardening) — each verified still-open first.
