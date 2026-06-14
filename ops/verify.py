#!/usr/bin/env python3
"""Utah self-verification — runs the test suite and records Utah's own test health
to ``~/.utah/run/verify.json`` (the same status-file convention as ``voice.json``).

WHY this lives in ops/ and imports NOTHING from ``utah`` on purpose: a verifier
must run and REPORT even when the package it checks is broken. If it imported the
product, a syntax error mid-build would crash the verifier instead of surfacing
"RED — collection error". Pure stdlib + subprocess(pytest). This is the muscle
that makes Utah *know its own state* — the one thing Ace never did.

Truth = pytest EXIT CODE (0 green; non-zero red; 124 = suite hung past the bound;
125 = pytest unrunnable). Two anti-false-alarm guards for running behind a live
autonomous builder that writes a module + its test ~every 20s:
  * QUIESCENCE — don't sample mid-write; wait until no *.py changed for QUIET secs.
  * TRIPLE-CONFIRM — a red must persist across 3 runs spanning ~50s; a moving
    cross-file write race clears within one write cadence and is reported as such.

And one liveness guard: a crashing --loop cycle publishes an honest ``error``
state and keeps looping — a dead verifier is a permanently stale verify.json,
which ``sica_goals`` and the daemon CLI read as Utah's self-knowledge.

Usage:
  python ops/verify.py            # one shot: write status, print JSON, exit 0/1
  python ops/verify.py --loop     # permanent: quiescence-gated continuous verify

Knobs (env, all test-injectable): UTAH_HOME, UTAH_ROOT, UTAH_PY, UTAH_TEST_DSN,
UTAH_VERIFY_QUIET, UTAH_VERIFY_INTERVAL, UTAH_VERIFY_TIMEOUT (suite bound in
seconds; default 1200 — full pytest here runs ~10–15 min under CPU load; launchd
``com.utah.verify`` sets 1500 for extra headroom).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger("utah.ops.verify")

HOME = Path(os.environ.get("UTAH_HOME", str(Path.home() / ".utah")))
RUN_DIR = HOME / "run"
STATUS = RUN_DIR / "verify.json"
ROOT = Path(os.environ.get("UTAH_ROOT", str(Path(__file__).resolve().parent.parent)))
PY = os.environ.get("UTAH_PY", str(HOME / "venv" / "bin" / "python"))
TEST_DSN = os.environ.get("UTAH_TEST_DSN", "host=/tmp port=5433 dbname=utah_test")
QUIET = int(os.environ.get("UTAH_VERIFY_QUIET", "40"))        # secs of no .py change = build paused
INTERVAL = int(os.environ.get("UTAH_VERIFY_INTERVAL", "90"))  # gap between green sweeps
_DEFAULT_SUITE_TIMEOUT = 1200  # was 600 — false-red rc 124 when concurrent pytest loads the box
SUITE_TIMEOUT = int(os.environ.get("UTAH_VERIFY_TIMEOUT", str(_DEFAULT_SUITE_TIMEOUT)))

#: Known macOS CoreML/onnxruntime stderr noise — never a test failure.
_NOISE = ("onnxruntime", "coreml", "context leak")


def _write_status(d: dict) -> None:
    """Atomic status write (mkstemp -> replace), mirroring utah.voice.state."""
    RUN_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(RUN_DIR), prefix=".verify", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(d, f)
        os.replace(tmp, STATUS)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _pyfiles() -> int:
    n = 0
    for base in ("utah", "tests"):
        for p in (ROOT / base).rglob("*.py"):
            if "__pycache__" not in p.parts:
                n += 1
    return n


def _changed_within(secs: int) -> bool:
    """True if any tracked *.py was modified within the last *secs* (build active)."""
    cutoff = time.time() - secs
    for base in ("utah", "tests"):
        for p in (ROOT / base).rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            try:
                if p.stat().st_mtime > cutoff:
                    return True
            except FileNotFoundError:
                continue
    return False


def _failure_lines(out: str) -> list[str]:
    """Pull the human-readable failure lines out of pytest output, dropping the
    known mac noise so a CoreML warning never reads as a regression."""
    return [
        ln for ln in out.splitlines()
        if (ln.startswith("FAILED") or "ERROR collecting" in ln
            or "Interrupted" in ln or ln.startswith("ERROR "))
        and not any(n in ln.lower() for n in _NOISE)
    ][:30]


def run_suite() -> tuple[int, float, list[str]]:
    """Run the full suite once, bounded. Returns (exit_code, duration_s, failure_lines).
    Never raises: a hung suite is rc 124, an unrunnable pytest is rc 125."""
    env = dict(os.environ, UTAH_TEST_DSN=TEST_DSN, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    start = time.time()
    try:
        # iCloud Desktop spawns "<name> 2.py"/".orig"/".bak" conflict copies that
        # pytest would collect as stale duplicate modules and fail on. They are never
        # source — ignore them so the gate reflects the real tree, not iCloud noise.
        proc = subprocess.run(
            [PY, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--tb=line", "-rf",
             "--ignore-glob=* 2.py", "--ignore-glob=* 2", "--ignore-glob=*.orig",
             "--ignore-glob=*.bak", "--ignore-glob=*.bak-*"],
            cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=SUITE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return 124, round(time.time() - start, 1), [f"suite timed out after {SUITE_TIMEOUT}s"]
    except OSError as exc:
        return 125, round(time.time() - start, 1), [f"could not run pytest: {exc}"]
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, round(time.time() - start, 1), _failure_lines(out)


def verify_once(runner: Callable[[], tuple[int, float, list[str]]] | None = None) -> dict:
    """One verification sample. *runner* is the suite seam (tests inject it;
    production uses the real bounded pytest run)."""
    rc, dur, fails = (run_suite if runner is None else runner)()
    return {
        "ok": rc == 0,
        "state": "green" if rc == 0 else "red",
        "exit_code": rc,
        "duration_s": dur,
        "pyfiles": _pyfiles(),
        "failures": fails,
        "ts": time.time(),
    }


def loop(*, verify_fn: Callable[[], dict] | None = None,
         write_fn: Callable[[dict], None] = _write_status,
         sleep_fn: Callable[[float], None] = time.sleep,
         changed_fn: Callable[[int], bool] | None = None,
         max_cycles: int | None = None) -> None:
    """Quiescence-gated continuous verify with triple-confirmed reds.

    Every seam is injectable so tests drive the full state machine without a
    real suite. A crashing cycle publishes ``{"state": "error"}`` and keeps
    looping — the verifier dying quietly would freeze verify.json at its last
    (possibly green) word, the dishonest-signal failure mode.
    """
    verify_fn = verify_once if verify_fn is None else verify_fn
    changed_fn = _changed_within if changed_fn is None else changed_fn
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        cycles += 1
        try:
            # 1) wait for the builder to pause (up to ~4 min); sample anyway if relentless
            waited = 0
            while changed_fn(QUIET) and waited < 240:
                write_fn({"ok": None, "state": "build_active",
                          "pyfiles": _pyfiles(), "ts": time.time()})
                sleep_fn(15)
                waited += 15

            res = verify_fn()
            # 2) triple-confirm a red so a moving cross-file write race can't false-alarm
            if not res["ok"]:
                sleep_fn(25)
                r2 = verify_fn()
                if r2["ok"]:
                    r2["note"] = "first run red, cleared on re-run (build-write race)"
                    res = r2
                else:
                    sleep_fn(25)
                    r3 = verify_fn()
                    if r3["ok"]:
                        r3["note"] = "cleared on 3rd run (build-write race)"
                    else:
                        r3["confirmed"] = True
                        r3["note"] = "RED confirmed across 3 runs (~50s) — real regression"
                    res = r3
            write_fn(res)
        except Exception as exc:  # noqa: BLE001 — loop boundary: publish honestly, keep going
            log.error("verify cycle failed: %s: %s", type(exc).__name__, exc)
            try:
                write_fn({"ok": False, "state": "error",
                          "error": f"{type(exc).__name__}: {exc}", "ts": time.time()})
            except Exception as wexc:  # noqa: BLE001 — even the error report is best-effort
                log.error("verify: cannot publish error state: %s", wexc)
        sleep_fn(INTERVAL)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--loop" in args:
        loop()
        return 0
    res = verify_once()
    try:
        _write_status(res)
    except (OSError, TypeError, ValueError) as exc:
        log.error("verify: cannot write verify.json: %s", exc)
        res["write_error"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(res, indent=2))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
