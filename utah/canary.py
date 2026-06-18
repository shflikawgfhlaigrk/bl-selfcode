"""Continuous self-proof — the canary re-earns "it works" every 10 minutes.

"Proven live" is a point-in-time fact and it rots: everything fixed in the
2026-06-11 hardening pass (deck blanking, dead feed-heal paths, an orphan
Sovereign daemon) had been proven once and silently regressed. The canary
turns proof into a standing condition: every check probes a REAL live surface
end-to-end (never a mock, never cached state), failures land in the failure
ledger (the deck's AUDIT panel) and page once through the alert dedup.

Checks are independent: one dead surface never hides another. A probe crash
is itself a failure, never an exception — the canary must outlive the things
it watches.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib
import urllib.request

from utah import failures

log = logging.getLogger("utah.canary")

DECK = "http://127.0.0.1:8766"
#: Sovereign Command Center — local demo install only (8775). Never 8765/8766 —
#: those are Utah (8766 loopback; 8765 tailnet via com.utah.tailserve).
SOVEREIGN_PORTS = (8775,)
#: A live WC feed ticks every few hundred ms; 120s of silence IN SESSION is dead.
TICK_MAX_AGE_S = 120.0


def _http_json(url: str, timeout: float = 8.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def futures_session_open(now: datetime.datetime | None = None) -> bool:
    """CME Globex equity-futures hours, Central Time: Sunday 17:00 through
    Friday 16:00 with a 16:00-17:00 maintenance halt each day. Outside these,
    a quiet feed is "market closed", not an outage — the deck learned the same
    lesson (10h-stale US ETF ticks rendered alarm-red overnight)."""
    if now is None:
        import zoneinfo

        now = datetime.datetime.now(zoneinfo.ZoneInfo("America/Chicago"))
    wd, t = now.weekday(), now.time()
    if wd == 5:                                   # Saturday — closed all day
        return False
    if wd == 6:                                   # Sunday — opens 17:00
        return t >= datetime.time(17)
    if wd == 4 and t >= datetime.time(16):        # Friday — closed from 16:00
        return False
    return not (datetime.time(16) <= t < datetime.time(17))   # daily halt


def check_deck() -> tuple[bool, str]:
    """The data plane Michael looks at: live, counting, and never silently blank."""
    st = _http_json(f"{DECK}/state")
    if st.get("health") != "live":
        return False, f"deck health={st.get('health')!r} — daemon unreachable from the bridge"
    counts = st.get("ledger") or {}
    if not counts.get("leads"):
        return False, "ledger counts empty — product data plane dark"
    if not st.get("leads") and not (st.get("degraded") or {}).get("ledger"):
        return False, "leads rows empty with NO degraded flag — silent blank is back"
    return True, f"leads={counts.get('leads')} fires={counts.get('fires')}"


def check_ticks() -> tuple[bool, str]:
    """Tick freshness — only judged while the futures session is open."""
    if not futures_session_open():
        return True, "market closed — freshness not judged"
    from utah.product.ledger import get_ledger

    ticks = get_ledger().live_ticks()
    if not ticks:
        return False, "no live ticks at all during an open session"
    # age_ms=0 is the FRESHEST possible tick — `or 9e12` once treated that falsy
    # zero as "missing" and called a perfectly live feed dead. Only None/unreadable
    # ages are skipped; a feed whose every age is unreadable is shape drift, not green.
    ages_s: list[float] = []
    for t in ticks:
        raw = t.get("age_ms")
        if raw is None:
            continue
        try:
            ages_s.append(float(raw) / 1000.0)
        except (TypeError, ValueError):
            continue
    if not ages_s:
        return False, "ticks present but no readable age_ms — feed shape drifted"
    age_s = min(ages_s)
    if age_s > TICK_MAX_AGE_S:
        return False, f"freshest tick {age_s:.0f}s old in session — feed dead or tab gone"
    return True, f"freshest tick {age_s:.1f}s old"


def check_chrome_wc() -> tuple[bool, str]:
    """The logged-in WC tab — its silent death cost 85 dark minutes on 2026-06-11."""
    from utah.integrations import wc_feed

    if wc_feed.feed_available():
        return True, "logged-in WC tab live on :9223"
    return False, "no logged-in WC page (tab gone, chrome down, or login wall)"


def check_sovereign() -> tuple[bool, str]:
    last = "no port answered"
    for port in SOVEREIGN_PORTS:
        try:
            st = _http_json(f"http://127.0.0.1:{port}/api/status", timeout=5)
        except Exception as exc:  # noqa: BLE001 — try the other port
            last = f":{port} {type(exc).__name__}"
            continue
        if st.get("ok"):
            return True, f"Command Center on :{port} · {st.get('agents')} agents"
        last = f":{port} answered ok={st.get('ok')}"
    return False, f"Sovereign unreachable ({last})"


def check_voice() -> tuple[bool, str]:
    v = _http_json(f"{DECK}/panel/voice")
    status = v.get("status")
    if status in (None, "", "down"):
        return False, f"voice loop status={status!r}"
    return True, f"voice status={status}"


def check_mail() -> tuple[bool, str]:
    from utah import mail

    if mail.creds_available():
        return True, "smtp creds present"
    return False, "gmail.json missing/empty — brief + outreach are dark"


def check_drift() -> tuple[bool, str]:
    """Written-vs-running drift — every class here has caused a real outage."""
    from utah import drift

    return drift.scan()


#: A foreign process gets two consecutive sightings (>=10 min apart) above this
#: before it's called a hog — one busy build is fine; a pinned core for 20 hours
#: (Claude Desktop, 2026-06-11) starved voice + shed the deck with no witness.
HOG_CPU_PCT = 90.0
_HOG_STATE = pathlib.Path(os.path.expanduser("~/.utah/run/canary-hogs.json"))


def check_hogs(ps_fn=None) -> tuple[bool, str]:
    """The environment fence: non-Utah processes camping a core, detected.

    Detection only — renice/kill stays a human (or explicitly flagged) action."""
    import subprocess

    def _ps() -> list[tuple[int, float, str]]:
        out = subprocess.run(["ps", "-axo", "pid=,pcpu=,comm="],
                             capture_output=True, text=True, timeout=10).stdout
        rows = []
        for line in out.splitlines():
            parts = line.split(None, 2)
            if len(parts) == 3:
                try:
                    rows.append((int(parts[0]), float(parts[1]), parts[2]))
                except ValueError:
                    continue
        return rows

    hot = {str(pid): f"{comm.rsplit('/', 1)[-1]} {cpu:.0f}%"
           for pid, cpu, comm in (ps_fn or _ps)()
           if cpu >= HOG_CPU_PCT and "/.utah/" not in comm}
    try:
        prev = set(json.loads(_HOG_STATE.read_text())) if _HOG_STATE.exists() else set()
    except (OSError, json.JSONDecodeError):
        prev = set()
    try:
        _HOG_STATE.parent.mkdir(parents=True, exist_ok=True)
        _HOG_STATE.write_text(json.dumps(sorted(hot)))
    except OSError:
        pass
    sustained = sorted(prev & set(hot))
    if sustained:
        return False, "sustained CPU hogs (2+ canary runs): " + \
            "; ".join(f"pid {p} ({hot[p]})" for p in sustained)
    return True, f"{len(hot)} hot process(es), none sustained"


CHECKS = {
    "deck": check_deck,
    "ticks": check_ticks,
    "chrome_wc": check_chrome_wc,
    "sovereign": check_sovereign,
    "voice": check_voice,
    "mail": check_mail,
    "drift": check_drift,
    "hogs": check_hogs,
}


def run_scheduled(probes: dict | None = None) -> dict:
    """Run every probe; record each failure to the ledger; page once (deduped).

    Never raises — a canary that dies of what it measures reports nothing."""
    probes = probes if probes is not None else CHECKS
    results: dict[str, dict] = {}
    failed: list[str] = []
    for name, fn in probes.items():
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001 — a crashed probe IS a failure
            ok, detail = False, f"probe crashed: {type(exc).__name__}: {exc}"
        results[name] = {"ok": ok, "detail": detail}
        if not ok:
            failed.append(name)
            failures.record("canary", name, detail)
            log.warning("canary FAIL %s: %s", name, detail)
    if failed:
        try:
            from utah import alerts

            alerts.critical_async(
                "canary",
                "; ".join(f"{n}: {results[n]['detail'][:80]}" for n in failed),
                key="canary:" + ",".join(sorted(failed)),
            )
        except Exception:  # noqa: BLE001 — paging is best-effort, recording is not
            log.warning("canary: alert dispatch failed", exc_info=True)
    # DETECT -> ACT: bump the autonomous healer for fires it can actually close (drifted/
    # uninstalled plists, daemon stale, Sovereign down, deck/daemon down). com.utah.heal
    # WatchPaths this sentinel and runs control.heal() with NO human in the loop — this is
    # what makes the canary close its own fires instead of only filing a repair forever.
    if any(n in {"drift", "sovereign", "deck"} for n in failed):
        try:
            import time as _time

            from utah import config as _cfg

            trig = _cfg.UTAH_HOME / "run" / "heal.trigger"
            trig.parent.mkdir(parents=True, exist_ok=True)
            trig.write_text(str(_time.time()))
        except Exception:  # noqa: BLE001 — trigger bump is best-effort
            log.debug("canary: heal trigger bump failed", exc_info=True)
    return {"ok": not failed, "failed": failed, "results": results}
