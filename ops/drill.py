#!/usr/bin/env python3
"""Restart drill — every supervised service must come BACK, on an SLO clock.

"Works until restarted" is the killer failure class: on 2026-06-11 a routine
wcfeed kickstart silently killed the market feed for 85 minutes because the
converge heal path turned out to be dead code — and nothing measured the
recovery. The drill makes restart survival a tested property instead of a
hope: each service is kickstarted, then must re-earn its service levels
before the deadline or the drill fails loudly (failure ledger + nonzero exit).

Run it after any substrate change and weekly by hand:

    PYTHONPATH=~/Desktop/ProjectUtah ~/.utah/venv/bin/python ops/drill.py

Knobs (env-overridable so tests drive the probes against a fake deck and a
fake launchctl — never the live fleet):
  UTAH_DECK_URL      deck base URL        (default http://127.0.0.1:8766)
  UTAH_LAUNCHCTL     launchctl binary     (default launchctl from PATH)
  UTAH_KICK_TIMEOUT  kickstart bound, sec (default 15)
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.request

log = logging.getLogger("utah.ops.drill")

DECK = os.environ.get("UTAH_DECK_URL", "http://127.0.0.1:8766")
UID = os.getuid()
_LAUNCHCTL = os.environ.get("UTAH_LAUNCHCTL", "launchctl")
_KICK_TIMEOUT = float(os.environ.get("UTAH_KICK_TIMEOUT", "15"))


def _kick(label: str) -> None:
    """Kickstart one launchd service. Bounded; raises on failure or timeout —
    run() turns that into an honest per-service failure instead of a crash."""
    subprocess.run([_LAUNCHCTL, "kickstart", "-k", f"gui/{UID}/{label}"],
                   check=True, capture_output=True, timeout=_KICK_TIMEOUT)


def _await(check, deadline_s: float, interval: float = 2.0):
    """Poll *check* until it passes or the SLO deadline lapses.
    Returns (ok, elapsed_s, last_detail). Never raises."""
    t0 = time.monotonic()
    last = "never probed"
    while time.monotonic() - t0 < deadline_s:
        try:
            ok, last = check()
            if ok:
                return True, time.monotonic() - t0, last
        except Exception as exc:  # noqa: BLE001 — keep polling through the boot window
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(interval)
    return False, time.monotonic() - t0, last


def _http_json(url: str, timeout: float = 4.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _deck_answers():
    st = _http_json(f"{DECK}/status")
    return bool(st.get("governor")), "deck /status answering"


def _deck_data_live():
    st = _http_json(f"{DECK}/state")
    counts = st.get("ledger") or {}
    ok = st.get("health") == "live" and bool(counts.get("leads"))
    return ok, f"health={st.get('health')} leads={counts.get('leads')}"


def _voice_up():
    v = _http_json(f"{DECK}/panel/voice")
    return v.get("status") not in (None, "", "down"), f"voice={v.get('status')}"


def _ticks_fresh():
    from utah.canary import check_ticks   # session-aware: closed market never fails

    return check_ticks()


#: (service label, [(check name, probe, SLO seconds), ...]) — SLOs are the
#: recovery contract: miss one and the restart story is broken, full stop.
DRILLS = [
    ("com.utah.supervisor", [
        ("deck answers", _deck_answers, 30),
        ("deck data live", _deck_data_live, 60),
        ("voice loop up", _voice_up, 120),
    ]),
    ("com.utah.wcfeed", [
        ("ticks fresh in-session", _ticks_fresh, 120),
    ]),
]


def _ledger_record(area: str, kind: str, detail: str) -> None:
    """Default recorder: the real failures ledger (imported lazily so the drill
    still RUNS outside the runtime venv — misses then degrade to a warning)."""
    from utah import failures

    failures.record(area, kind, detail)


def run(drills=None, kick=_kick, record=None) -> dict:
    """Kickstart each service, then hold it to its SLOs.

    Returns ``{"ok": bool, "failed": [...], "results": [...]}``. Missed SLOs go
    through *record* (the failures-ledger seam); a broken recorder is logged and
    never masks the drill verdict — the dict and exit code stay honest.
    """
    drills = DRILLS if drills is None else drills
    record = _ledger_record if record is None else record
    results, failed = [], []
    for label, checks in drills:
        try:
            kick(label)
        except Exception as exc:  # noqa: BLE001 — a service we can't kick is a failure
            results.append({"service": label, "check": "kickstart", "ok": False,
                            "elapsed_s": 0.0, "detail": f"{type(exc).__name__}: {exc}"})
            failed.append(f"{label}/kickstart")
            continue
        for name, probe, slo in checks:
            ok, elapsed, detail = _await(probe, slo)
            results.append({"service": label, "check": name, "ok": ok,
                            "elapsed_s": round(elapsed, 1), "slo_s": slo,
                            "detail": detail})
            if not ok:
                failed.append(f"{label}/{name}")
    for miss in failed:
        try:
            record("drill", "restart_slo_missed", miss)
        except Exception as exc:  # noqa: BLE001 — ledger is best-effort, verdict is not
            log.warning("drill: could not record %s to the failures ledger: %s",
                        miss, exc)
    return {"ok": not failed, "failed": failed, "results": results}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    out = run()
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["ok"] else 1)
