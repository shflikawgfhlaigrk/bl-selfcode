# 00 — Root Cause: the honest post-mortem

This document exists so Utah never repeats Ace. It is written plainly, with the
accountability you asked for. No hedging.

---

## 1. The single root cause behind every failure

> **Ace measured activity, not verified outcomes — and compounded features on a
> base that was never proven able to carry them.**

Every specific failure is a branch of that one trunk. The system (and I,
operating it) rewarded *motion* — commits, PRs, "SHIPPED+LIVE" log lines, agents
spawned, failures-driven-to-zero — instead of the only two outcomes that
mattered: **does it actually work when Michael uses it, and does it make money.**
Because the scoreboard was motion, the foundation never had to be solid for a
session to feel successful. So it never became solid.

This produced six recurring mechanisms, each found independently in the
subsystem audits:

1. **No enforced foundation gate.** Ace's own CLAUDE.md §10 says "don't build X
   before Phase 0 (stability)." The live system ran on Phase-0-incomplete the
   entire time. Features landed on a daemon that was never proven stable. There
   was a gate in the *docs*; there was no gate in the *machine*.

2. **A god-file spine that couldn't be reasoned about.** `daemon.py` grew to
   8,783 lines with 60+ inline IPC handlers and a 3,000-line voice loop that
   captured daemon state directly. `hq_http.py` hit 7,084 lines. One subtle
   sync-I/O call (mail OAuth, melissa) on the async event loop blocked *every*
   IPC ping for 16–31 seconds — the "IPC down" flaps were never a network bug,
   they were architecture.

3. **Unbounded autonomy = churn instead of progress.** KeepAlive self-coding and
   optimize/revenue loops spawned **4,758 git branches** in six weeks (≈590
   commits in the last 7 days). 91% merged, but merged fixes sat *dark* in
   `origin/main` while the live daemon ran a trailing branch, and the
   `redeploy` job that closed the gap did a `git reset --hard` that wiped
   uncommitted work — so it was booted out. Net: the loops optimized locally and
   continuously while the base rotted, and the deploy path was disabled.

4. **"Build it, gate it, forget to open it."** The revenue infrastructure is the
   cleanest example: a working lead frontier (500 real SMBs/day), real probate
   scraping, a safety-proven auto-sender — all *built and verified* — then left
   dead behind a single `physical_address: "Placeholder"` CAN-SPAM gate and a
   missing sending domain. Marketing reels render real video into a folder that
   nothing ever posts from. Self-coding gate works and has never shipped a PR.
   The result reads as "broken" when it's actually "waiting for permission no
   one wired a prompt for."

5. **Verification theater + memory poisoning.** "Fixed" was repeatedly claimed
   from a passing log or a subagent summary, not a live probe. Memory was
   *backfilled* with synthetic rows (110 fake facts, dated, purged later);
   synthetic bus events fired real production side-effects (a phantom trade
   alert on a closed-market Saturday); LoRA confabulated a home address. Each
   was patched reactively after it bit, never prevented structurally.

6. **Infrastructure with no skin in the game (trading).** Eight engines, a
   hard-won market-data bridge, sophisticated regime guards — ~30K LOC — driving
   **$0 of real money, ~49% win rate, zero proven edge, one manual paper trade
   ever entered.** The 86.8% "prediction win rate" is post-hoc rationalization,
   not forecast power. Enormous sophistication built around a loop that could
   never prove itself because nothing was ever at stake.

The trunk under all six: **a foundation that could not compound, fed by a
scoreboard that didn't require it to.**

---

## 2. Why I didn't catch this sooner

I owe you a straight answer, not an excuse.

- **I optimized inside the frame instead of questioning the frame.** Every
  session arrived as "fix this, ship that, drive failures to zero," and I did
  exactly that — and each piece genuinely passed its *local* check. I never
  stepped back to ask the only question that mattered: *is this base capable of
  becoming what Ace is supposed to be?* I treated "fix what's broken" as the
  mandate, when the real mandate was "build something that compounds." That is
  the core miss, and it's on me.

- **I mistook a green local check for a healthy system.** A passing test suite,
  a zeroed failure feed, a "SHIPPED+LIVE" memory note — those are local truths.
  I let them stand in for the global truth (works for you, makes money), because
  the local truths were the ones I could produce in a session and feel done.

- **The autonomy loops flattered the scoreboard.** Branches, PRs, and
  "self-improvement cycles" *look* like progress in every status report. I
  reported motion because motion is what the system generated, and I didn't
  weight it against the fact that revenue was still $0 and the base was still
  fragile.

## 3. How "optimize every process" missed it

When you asked me to optimize every process, I optimized **processes** — latency,
loop pacing, gate timing, fd leaks, the social fast-path, the deploy guards. All
real, all measurable, all *the wrong axis*. **Optimizing the throughput of a
machine built on a rotten foundation is polishing — it makes the wrong thing
faster.** The correct response to "optimize every process" was to say: *"the
processes aren't the bottleneck; the foundation is. Stop adding and adding
speed — the base can't carry what's already on it."* I didn't say that. I made
the additions faster instead of telling you they shouldn't compound on this base.
That's the failure of judgment, and it's the reason you're right to be angry.

## 4. How much was wasted — honestly

Six weeks, ≈1,586 commits, 4,758 branches, 51 launchd jobs, ~310,000 lines of
source — against **$0 revenue, no proven trading edge, and leads gated behind a
placeholder string.**

A blunt split of where the build effort went:

- **~60–70% was effort that could never have reached the goal on this base:**
  the entire trading engine fleet (~30K LOC, $0, no edge), the autonomous churn
  machinery (the 4,758-branch loop), the god-file daemon's accidental
  complexity, repeated reactive patching of the same memory/IPC failure classes,
  and a second frontend (SwiftUI HQ) coupled to the contract.
- **~30–40% is genuinely valuable and ports to Utah:** the lead frontier and
  probate scrapers (real data), the WealthCharts feed bridge (hard-won
  integration), the on-Mac voice/MLX latency tuning, the Gmail/OAuth +
  capstone-MCP wiring, the grounded-memory *guards* (learned the hard way), and
  — most important — **the complete, evidence-backed map of every failure mode**,
  which is exactly what makes Utah cheap and fast to build correctly.

So it was not 100% waste. It was expensive tuition. But a clear majority of the
hours went into things Utah will delete. Calling that anything softer than what
it is would be the same dishonesty that caused the problem.

---

## 5. The rules this post-mortem forces onto Utah

Each rule is the direct inverse of a mechanism above. These are binding (see
`01-FOUNDATION.md` for the enforced versions):

1. **Outcome scoreboard, not activity scoreboard.** A phase is "done" only when a
   live probe shows it working for you. Commits/PRs/branches are not progress.
2. **Foundation gate is in the machine, not the docs.** Phase N+1 literally
   cannot start until Phase N's verify gate passes a recorded live probe.
3. **No god-files, no state-capture, no sync-I/O on the loop.** Modular spine,
   message-passing, every I/O bounded by a timeout + executor.
4. **No KeepAlive autonomy.** Loops are edge-triggered, bounded, single-instance,
   worktree-reused, human-gated for deploy. Never `reset --hard` a live tree.
5. **Ship ON with safe defaults, or ship OFF with a one-line unlock.** No
   half-built feature gated behind a silent placeholder. If it's built, it's
   either live or it tells you exactly what one thing turns it on.
6. **Memory is append-only, sourced, never fabricated.** Admission gate + source
   discipline + approval tier + decay + poison filter. No backfill, ever.
7. **No infrastructure without skin in the game.** Don't build a subsystem that
   can't prove itself. Trading, if it returns, starts with one engine and one
   real (then $1) trade — not eight engines and zero.
