# 13 — AUTONOMY audit (self-improvement, the multiplier)

> The compounding loop — what runs *after* baseline is proven. Dirt + doctrine.

## 1. What we had
- KeepAlive self-coding (`agent_007`) + optimize/revenue loops; a verification
  ladder; `redeploy`/auto-pr-merger; **4,758 git branches** in 6 weeks.
- Output measured in commits/PRs/"SHIPPED+LIVE"; the self-coding gate sat **RED**;
  `redeploy` did `git reset --hard` on the live tree.

## 2. Why we did it
The mandate was "run forever, improve yourself, don't make me babysit." Continuous
loops + a self-coding agent were the literal interpretation: keep generating fixes.

## 3. What we didn't think about
- **Unbounded autonomy = churn, not progress** (consolidation < churn); the loops
  optimized locally while the base rotted.
- **Activity ≠ outcome** — branches/PRs looked like progress; revenue stayed $0.
- **Deploy wiped work** (`reset --hard`); merged fixes ran *dark*.
- **No graduated autonomy enforced** — the ladder was red and bypassable; no real
  tier gating; no kill-switch proof.
- It ran **before the base was proven** (compounded on sand).

## 4. What we're gonna change
- **Autonomy compounds ONLY on a green baseline** (the inversion of Ace): every
  baseline capability live + gated first; autonomy then multiplies it.
- **Bounded, edge-triggered, single-instance** (no KeepAlive); triggered by edges
  (a failed capability, a push), **worktree-reused** (no branch spam),
  **provenance-stamped**.
- **Tiered self-coding A/B/C/D as policy-as-data** (Tier-A autonomous on green after
  N supervised; B batch-review; C 5-rung; D off-limits byte-checked) + a **nightly
  kill-switch smoke test** that must refuse self-edits to safety.
- **Goal generation (4 passes:** failed-capabilities, intent-gaps, contradictions,
  world-model anomalies) → ranked queue; **skill library** (composable, self-
  verified, ≥40% reuse).
- **Deploy = stash-verify-swap, never `reset --hard`;** human-gated by default;
  readiness-probe after.
- **Scoreboard = verified outcomes** (works for Michael, makes money), not commits.
- **14-day AGI bar** (all conditions green) as the falsifiable "it compounds" proof.

## 5. How it helps
- It **multiplies instead of churns** — Ace's "once it's built everything
  multiplies" becomes true because the base actually holds.
- **Can't reset the tree, can't self-edit safety, can't spam branches**; every
  autonomous change is provenance-tracked and gate-passed.
- Outcome-gated, so "self-improving" means measurable improvement, not motion.

Sources: tiered/graduated self-coding + kill-switch — Ace's own AGI-BLUEPRINT §2.6;
worktree-reuse + provenance + bounded loops per the Processes audit (web: launchd
backoff, supervision).
