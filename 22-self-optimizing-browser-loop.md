# 22 — The Self-Optimizing Browser Loop (feasibility & hard limits)

> Ace uses its **own browser** to find optimizations in the **running product**, then
> deploys **Claude-CLI bots** to implement them — gated, scored, and merged without a
> human in the loop. This document records how that works, why the conventional wisdom
> says it "can't be done," how Utah's *bounded* design answers each objection, and —
> just as important — **exactly what it deliberately cannot do.**

Status: **LIVE + PROVEN** (2026-06-07). Discovery half added in `b5965a6`; the
implement/merge half (`sica_overseer` → `selfcode.propose_governed`) was already proven
(see [[utah-sica-selfcoding]]). This doc is the honest map, not a sales sheet.

---

## 1. The loop, end to end

```
              ┌─────────────────────────── com.utah.selfcode (launchd, every 6h) ──────────────────────────┐
              │                                                                                             │
  DISCOVER ───┤  sica_goals.next_task(domain)   domain rotates: baseline · leads · autonomy · frontend      │
              │     • frontend  → Ace's OWN headless-Chrome browser renders its OWN live deck (:8766)        │
              │                    summarizes the REAL rendered DOM (dormant/empty/gated/error markers)      │
              │     • baseline  → verifier state / failing tests        • leads → live Postgres ledger        │
              │     • autonomy  → the self-improver's own archive                                            │
              │            ↓ grounded signal → brain (claude -p, no tools) → ONE concrete, safe task          │
  IMPLEMENT ──┤  selfcode.propose_governed(task)  in an ISOLATED clone (~/.utah/selfcode-repo)               │
              │     run_claude = sica_overseer.run_claude_supervised  → a `claude -p` BOT with Edit/Write/    │
              │     Read/Bash codes the fix, watched by the overseer (kill on 300s / $10 / stall)            │
  GATE ───────┤  the full test suite must pass · safety-core byte-check · tier classification                │
  MERGE ──────┤  Tier-A + green + supervised-ramp-met → auto-merge to the clone's main → push                │
  PROPAGATE ──┤  sica_autonomy.propagate → fast-forward-ONLY merge into the live ProjectUtah (clean tree)    │
  SCORE ──────┤  sica.utility → archive → next cycle picks better tasks → it COMPOUNDS                        │
              └─────────────────────────────────────────────────────────────────────────────────────────────┘
```

Two halves, both real:

- **Discovery** (`utah/sica_goals.py`): the `frontend` domain renders the live deck with
  `utah/integrations/browser.py` (headless Chrome `--dump-dom`), reads the **actual**
  rendered DOM, and hands the brain a signal grounded in what was on screen. The brain
  proposes one small, safe optimization to `utah/interface/*`.
- **Implementation** (`utah/sica_overseer.py`, `utah/selfcode.py`): a real `claude -p`
  subprocess — the "CLI bot" — edits files in an isolated repo under supervision, then the
  change runs the gauntlet below.

**Proven live:** on first run, Ace's browser found that its **own live deck won't render
under headless Chrome** (the persistent SSE `EventSource` keeps `--dump-dom` from ever
settling), and the brain proposed the correct fix — *"gate the `EventSource` behind a
`navigator.webdriver`/`?static` check so headless snapshots settle while real users still
stream."* A genuine optimization, discovered by Ace looking at its own face.

---

## 2. "This can't be done" — the objections, and the bounded answers

The reason autonomous self-improving software is treated as a non-starter is a stack of
real failure modes. Utah does not wish them away; it **boxes** each one.

| Objection (why it "can't" work) | How Utah bounds it |
|---|---|
| **Unbounded self-modification diverges / goes rogue.** | The self-coder may never edit its own safety. `selfcode.SAFETY_PATHS` (Tier D = `selfcode.py`, `config.py`, `brain.py`, `peercred.py`, `lifecycle.py`, `governor.py`) is **byte-checked** around every run; a run that changes any of those bytes is rolled back. The improver cannot loosen its own guards. |
| **LLMs hallucinate fixes that look right and aren't.** | A change is discarded unless the **entire test suite passes** (`propose_governed`). The brain runs under the no-fabrication contract. Discovery is grounded in the **real rendered DOM / live DB / failure log**, not a guess. |
| **Auto-merging machine-written code is reckless.** | Tier policy (`POLICY`, doc-13). Only **Tier-A leaf** code auto-merges, and only after a **supervised ramp** (N human-reviewed greens). Money (`utah/product/`, `memory.py`, `store/` = Tier B), the spine + schema (`daemon/`, `migrations/` = Tier C), and the safety core (Tier D) **never** auto-merge — they stay reviewed proposals. |
| **A runaway coding run burns time/money/CPU.** | The **overseer** polls the live run and kills it on a hard limit (300 s wall / $10) or a stall (output unchanged across polls). A killed run is scored as a timeout — no partial merge. |
| **It will corrupt the live tree / fight the developer.** | All autonomous git happens in an **isolated clone**. Propagation into live is **fast-forward-only** and **refuses a dirty tree** — it can never clobber uncommitted work or force a divergent history. |
| **"Optimization" has no ground truth, so it optimizes noise.** | Discovery is tied to observable reality: the rendered DOM, the verifier's pass/fail, the Postgres ledgers, the durable failure log. The brain is asked for *one* concrete change grounded in that signal — not "make it better" in the abstract. |
| **Self-improvement recurses without bound.** | `MetaLoop` runs **one** step per cycle; cycles are scheduled (every 6h), scored, and archived. There is no inner recursion. Improvement compounds across *gated* cycles, not within an unbounded loop. |

The design philosophy: **compounding without divergence.** Every change is small,
grounded, gated, scored, reversible, and off the critical path until it earns trust.

---

## 3. What it deliberately **cannot** do (the honest boundary)

This is the part the headline usually omits. These are not bugs; they are the box.

1. **It cannot touch its own safety core.** Tier-D files are byte-locked. The improver
   cannot edit the suite gate, the tier policy, the no-fab brain, the auth, the
   singleton, or the admission governor — by construction, not by good behavior.
2. **It cannot autonomously ship money/spine/memory changes.** Tier B/C produce reviewed
   proposals only. A human merges those. So "self-improving revenue code" is *assisted*,
   not autonomous.
3. **It cannot exceed its compute/time budget per attempt.** 300 s / $10, hard. A fix
   that needs a larger change than that simply isn't made autonomously.
4. **It can only optimize what it can observe.** It sees the rendered DOM, the DB, the
   logs, the test results. It does **not** see user intent, market conditions, off-screen
   bugs, or anything without a signal. No signal → no grounded task → it falls back to a
   safe default. It cannot discover a problem it cannot measure.
5. **It cannot snapshot a live page that never goes quiet.** Headless `--dump-dom` waits
   for network-idle; the live deck's persistent SSE means it never settles. The loop
   handles this honestly (brief live attempt → `/sim` structural twin → report the hang as
   a finding) rather than pretending it rendered. This is a real limit of the rendering
   tool, documented, not hidden. A true live snapshot would require the DevTools Protocol
   (navigate → fixed wait → `DOM.getDocument`), which is not built.
6. **It is only as good as the suite and the brain.** The gate is the test suite: a weak
   or missing test for an area means a weak gate there. A wrong-but-plausible fix that
   still passes tests can merge (at Tier A). The suite is the ceiling on safety, and it is
   finite.
7. **It does not improve unattended forever toward a goal.** There is no objective
   function being maximized to convergence — just a rotating set of grounded, bounded
   tasks. It will not "wake up" more capable in a way the tier policy doesn't permit.

If someone claims a system that *autonomously, without bound, rewrites all of itself
including its own guardrails and ships to production* — **that** is the thing that can't
be done safely, and Utah does not attempt it. What Utah does is the achievable, useful
subset: **grounded discovery + gated, bounded, reversible implementation that compounds.**

---

## 4. Where it lives

| Concern | Code |
|---|---|
| Discovery (incl. browser `frontend` domain) | `utah/sica_goals.py` |
| The browser (headless Chrome `--dump-dom`, `timeout`) | `utah/integrations/browser.py` |
| Bot deployment under supervision | `utah/sica_overseer.py` (`run_claude_supervised`) |
| Gate · tier policy · auto-merge · safety byte-check | `utah/selfcode.py` (`propose_governed`, `POLICY`, `SAFETY_PATHS`) |
| Cycle · isolated repo · ff-only propagation | `utah/sica_autonomy.py` (`run_cycle`, `propagate`) |
| Scoring · archive · compounding | `utah/sica.py`, `utah/sica_loop.py` |
| Schedule | launchd `com.utah.selfcode` (every 6h) · pause = `touch ~/.utah/run/selfcode.disabled` |

Tuning seams (env): `UTAH_DASHBOARD_URL`, `UTAH_DASHBOARD_SIM_URL`,
`UTAH_FRONTEND_RENDER_TIMEOUT`, `UTAH_SELFCODE_REPO`, `UTAH_LIVE_REPO`.

---

## 5. The next honest step

The deepest current limit is **#5/#6**: discovery sees structure (via `/sim`) and live
*data* (via the JSON endpoints the deck already exposes), but not a true headless snapshot
of the live page, and the safety ceiling is the test suite. The high-value follow-ups,
in order: (a) a DevTools-Protocol snapshot path so the browser can capture the live deck
verbatim; (b) feed the live JSON state into `frontend` discovery so the brain critiques
real data, not just structure; (c) grow suite coverage on Tier-A surfaces so the gate is
stronger where auto-merge is allowed. None of these remove a boundary — they sharpen the
discovery and the gate inside it.
