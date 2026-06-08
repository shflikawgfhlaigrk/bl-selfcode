"""SICA goal source — WHAT the autonomous loop optimizes.

Replaces archive-mimicry (trivial `_meta` files) with REAL, domain-targeted
improvement tasks. Each cycle rotates across domains and asks the brain for ONE
concrete, tier-aware task grounded in LIVE signals:

  baseline  — code/test health: fix a flaky/failing test, add missing coverage,
              clear a failure-log entry. (leaf Tier-A / spine Tier-C.)
  leads     — the revenue pipeline: lead volume / sources / dedup / contact
              enrichment, grounded in the Postgres leads ledger + product/leads.py.
              (Tier-B → stays a reviewed proposal, never auto-merged.)
  autonomy  — the self-improver itself: the meta-loop / scoring / overseer
              (sica_loop|sica_autonomy|sica_overseer|sica_goals — the NON-safety
              parts; the Tier-D core selfcode/config/brain stay off-limits).
              "how to be autonomous": make it pick better tasks, score, compound.
  frontend  — Ace uses its OWN browser (headless Chrome) to look at its OWN live
              command deck, observe what actually rendered (dormant/empty panels,
              broken affordances), and propose a grounded UX/frontend optimization
              to utah/interface/* (leaf Tier-A → the Claude-CLI bot's fix can
              auto-merge after the supervised ramp). Discovery is grounded in the
              real rendered DOM, never a guess about markup the brain never saw.

Every signal reader is defensive (never raises) and injected so this is
unit-proven without DB/brain/browser.
"""
from __future__ import annotations

import json
import logging
import os

from utah import config, sica
from utah.daemon import runtime

log = logging.getLogger("utah.sica_goals")

DOMAINS = ("baseline", "leads", "autonomy", "frontend")
CYCLE_N = runtime.RUN_DIR / "selfcode-cycle.n"
VERIFY_JSON = runtime.RUN_DIR / "verify.json"
#: Ace's own live deck — what the browser renders during frontend discovery.
DASHBOARD_URL = os.environ.get("UTAH_DASHBOARD_URL", "http://127.0.0.1:8766/")
#: The quiescent structural twin (/sim) — FALLBACK only. The live deck renders now
#: (browser.py caps --dump-dom with --timeout so the persistent SSE no longer hangs it);
#: /sim carries the same panel structure if a live render ever fails.
DASHBOARD_SIM_URL = os.environ.get("UTAH_DASHBOARD_SIM_URL", "http://127.0.0.1:8766/sim")
#: Bound the live-deck render so a slow/hung Chrome falls back to /sim instead of blocking
#: (the live deck normally dumps in ~2s; this is the safety cap, not a fast-fail-by-design).
_LIVE_RENDER_TIMEOUT_S = int(os.environ.get("UTAH_FRONTEND_RENDER_TIMEOUT", "8"))


def pick_domain(n: int) -> str:
    """Round-robin so baseline/leads/autonomy all get attention over time."""
    return DOMAINS[n % len(DOMAINS)]


def next_cycle_index() -> int:
    """Read + increment a persistent cycle counter (drives the domain rotation)."""
    try:
        n = int(json.loads(CYCLE_N.read_text()).get("n", 0))
    except Exception:  # noqa: BLE001 - absent/corrupt → start at 0
        n = 0
    try:
        CYCLE_N.parent.mkdir(parents=True, exist_ok=True)
        CYCLE_N.write_text(json.dumps({"n": n + 1}))
    except Exception as exc:  # noqa: BLE001
        log.warning("cycle counter write failed: %s", exc)
    return n


# ── live signal readers (defensive; injectable) ──────────────────────────────
def _db_query(sql: str):
    import psycopg
    with psycopg.connect(config.DB_DSN, connect_timeout=8) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()


def _leads_signal(db_query=None) -> str:
    q = db_query or _db_query
    try:
        by_src = q("SELECT source, count(*) FROM leads GROUP BY source ORDER BY 2 DESC")
        total = sum(n for _, n in by_src)
        with_contact = q("SELECT count(*) FROM leads WHERE contact IS NOT NULL AND contact <> '{}'::jsonb")[0][0]
        probate = q("SELECT count(*) FROM probate")[0][0]
        srcs = ", ".join(f"{s}={n}" for s, n in by_src) or "none"
        return (f"leads total={total} by source [{srcs}]; with_contact={with_contact}; "
                f"probate_rows={probate}. Capability: utah/product/leads.py (OSM/Overpass, "
                f"national-chain + has-website filters, UNIQUE(name,region) dedup).")
    except Exception as exc:  # noqa: BLE001
        return f"(leads signal unavailable: {type(exc).__name__}); capability: utah/product/leads.py"


def _baseline_signal(read_text=None) -> str:
    rt = read_text or (lambda p: p.read_text() if p.exists() else "")
    try:
        verify = json.loads(rt(VERIFY_JSON) or "{}")
        state = verify.get("state", "unknown")
        detail = str(verify.get("detail", verify.get("failing", "")))[:200]
        return (f"verifier state={state} detail={detail!r}. Improve code/test health: "
                f"fix a flaky/failing test, add coverage for an untested branch, or clear "
                f"a real failure-log entry. Keep changes leaf-level (Tier-A) where possible.")
    except Exception as exc:  # noqa: BLE001
        return f"(baseline signal unavailable: {type(exc).__name__}); improve test coverage/health."


def _autonomy_signal(archive=None) -> str:
    arch = archive or sica.Archive()
    try:
        entries = arch.entries()
        best = max((e.get("utility", 0) for e in entries), default=0)
        fails = sum(1 for e in entries if not e.get("passed"))
        return (f"self-coding archive: {len(entries)} attempts, best_utility={best}, "
                f"{fails} failed. Improve the autonomy machinery (utah/sica_loop.py, "
                f"utah/sica_autonomy.py, utah/sica_overseer.py, utah/sica_goals.py) — better "
                f"task selection, scoring, overseer, or compounding. Do NOT touch the Tier-D "
                f"safety core (selfcode.py/config.py/brain.py).")
    except Exception as exc:  # noqa: BLE001
        return f"(autonomy signal unavailable: {type(exc).__name__})"


#: Telltale markers the deck renders for dormant / broken / empty / loading states —
#: real signals the brain can target. (The deck advertises GATED/DORMANT honestly.)
_DOM_MARKERS = ("DORMANT", "GATED", "unavailable", "OFFLINE", "no producer",
                "LOADING", "error", "awaiting")


def _summarize_dom(html: str) -> str:
    """Cheap, real observations from the rendered DOM — no model, no fabrication."""
    low = html.lower()
    found = {m: low.count(m.lower()) for m in _DOM_MARKERS}
    nz = {k: v for k, v in found.items() if v}
    return f"{len(html)} chars rendered; state markers {nz or 'none'}"


def _frontend_signal(render_fn=None) -> str:
    """Ace looks at its OWN face with its OWN browser: render the deck via headless
    Chrome and report what actually came back, so the brain proposes a REAL, grounded
    frontend/UX optimization — not a guess about markup it never saw.

    Renders the LIVE deck (/) FIRST — ``browser.render`` now bounds ``--dump-dom`` with a
    settle cap (``--timeout``), so the persistent-SSE deck dumps its real live DOM (~2s)
    instead of hanging. Falls back to the quiescent ``/sim`` twin only if the live render
    fails, then to a safe generic prompt if BOTH fail. Never raises."""
    from utah.integrations import browser

    fn = render_fn or browser.render

    def _try(url, **kw):
        try:
            return fn(url, **kw) if render_fn is None else fn(url)
        except Exception as exc:  # noqa: BLE001 — discovery is best-effort
            return {"rendered": False, "error": f"{type(exc).__name__}: {exc}", "url": url}

    # Look at the REAL live deck first — browser.py caps --dump-dom with --timeout so the SSE
    # stream no longer hangs it (proven: the live deck dumps ~86k chars in ~2s). Fall back to the
    # quiescent /sim structural twin only if the live render fails, then to a generic prompt.
    live = _try(DASHBOARD_URL, timeout=_LIVE_RENDER_TIMEOUT_S)
    if live.get("rendered"):
        return (f"Ace rendered its OWN LIVE deck {DASHBOARD_URL} through headless Chrome and "
                f"observed: {_summarize_dom(live.get('html', ''))}. " + _FRONTEND_ASK)
    twin = _try(DASHBOARD_SIM_URL)
    if twin.get("rendered"):
        return (f"Ace's live deck didn't render ({live.get('error', 'render failed')}); rendered the "
                f"quiescent structural twin {DASHBOARD_SIM_URL} instead and observed: "
                f"{_summarize_dom(twin.get('html', ''))}. " + _FRONTEND_ASK)
    why = "no headless Chrome installed" if twin.get("gated") else twin.get("error", "render failed")
    return (f"(browser frontend discovery unavailable: {why}). Propose a small, safe frontend "
            "robustness fix to utah/interface/static/live.html or utah/interface/web.py.")


def observe_deck(render_fn=None) -> dict:
    """Render the live deck (fallback /sim) → a STRUCTURED observation
    ``{rendered, url, chars, markers}``. Lets the self-code loop re-look at its OWN face
    AFTER a frontend change and record what actually rendered — the closed self-optimizing
    browser loop (verify in the real UI, not guess). Never raises; ``rendered=False`` if
    neither URL renders."""
    from utah.integrations import browser

    fn = render_fn or browser.render

    def _try(url, **kw):
        try:
            return fn(url, **kw) if render_fn is None else fn(url)
        except Exception:  # noqa: BLE001 — observation is best-effort
            return {"rendered": False, "url": url}

    for url, kw in ((DASHBOARD_URL, {"timeout": _LIVE_RENDER_TIMEOUT_S}), (DASHBOARD_SIM_URL, {})):
        r = _try(url, **kw)
        if r.get("rendered"):
            html = r.get("html", "") or ""
            low = html.lower()
            markers = {m: low.count(m.lower()) for m in _DOM_MARKERS if low.count(m.lower())}
            return {"rendered": True, "url": url, "chars": len(html), "markers": markers}
    return {"rendered": False, "url": DASHBOARD_URL, "chars": 0, "markers": {}}


_FRONTEND_ASK = (
    "Propose ONE concrete, SAFE frontend/UX optimization to utah/interface/static/live.html or "
    "utah/interface/web.py — make a dormant/empty panel more honest, fix a missing/broken "
    "affordance, improve clarity or accessibility, or fix a render/latency issue — grounded in "
    "what was ACTUALLY rendered. Touch one file."
)


def gather_signals(domain: str, **inject) -> str:
    if domain == "leads":
        return _leads_signal(db_query=inject.get("db_query"))
    if domain == "autonomy":
        return _autonomy_signal(archive=inject.get("archive"))
    if domain == "frontend":
        return _frontend_signal(render_fn=inject.get("render_fn"))
    return _baseline_signal(read_text=inject.get("read_text"))


_PROMPT = (
    "You are Utah's autonomous self-improver. Domain THIS cycle: {domain}.\n"
    "Live signals:\n{signals}\n\n"
    "Propose the SINGLE next concrete improvement task IN THIS DOMAIN: one sentence, "
    "actionable, small, and SAFE. Touch as few files as possible. NEVER edit the safety "
    "core (utah/selfcode.py, utah/config.py, utah/brain.py, utah/daemon/peercred.py, "
    "lifecycle.py, governor.py). Reply with ONLY the task text."
)


def build_prompt(domain: str, signals: str) -> str:
    return _PROMPT.format(domain=domain, signals=signals)


def next_task(domain: str, *, brain_fn, **inject) -> str:
    """Domain signals → prompt → brain → one grounded task (stripped)."""
    signals = gather_signals(domain, **inject)
    return (brain_fn(build_prompt(domain, signals)) or "").strip()


__all__ = ["DOMAINS", "pick_domain", "next_cycle_index", "gather_signals",
           "build_prompt", "next_task", "observe_deck", "CYCLE_N", "DASHBOARD_URL"]
