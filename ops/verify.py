#!/usr/bin/env python3
"""Utah self-verification — runs the test suite and records Utah's own test health
to ``~/.utah/run/verify.json`` (the same status-file convention as ``voice.json``).

WHY this lives in ops/ and imports NOTHING from ``utah`` on purpose: a verifier
must run and REPORT even when the package it checks is broken. If it imported the
product, a syntax error mid-build would crash the verifier instead of surfacing
"RED — collection error". Pure stdlib + subprocess(pytest). This is the muscle
that makes Utah *know its own state* — the one thing Ace never did.

Truth = pytest EXIT CODE (0 green; non-zero red). Two anti-false-alarm guards for
running behind a live autonomous builder that writes a module + its test ~every
20s:
  * QUIESCENCE — don't sample mid-write; wait until no *.py changed for QUIET secs.
  * TRIPLE-CONFIRM — a red must persist across 3 runs spanning ~50s; a moving
    cross-file write race clears within one write cadence and is reported as such.

Usage:
  python ops/verify.py            # one shot: write status, print JSON, exit 0/1
  python ops/verify.py --loop     # permanent: quiescence-gated continuous verify
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = Path(os.environ.get("UTAH_HOME", str(Path.home() / ".utah")))
RUN_DIR = HOME / "run"
STATUS = RUN_DIR / "verify.json"
ROOT = Path(os.environ.get("UTAH_ROOT", str(Path(__file__).resolve().parent.parent)))
PY = os.environ.get("UTAH_PY", str(HOME / "venv" / "bin" / "python"))
TEST_DSN = os.environ.get("UTAH_TEST_DSN", "host=/tmp port=5433 dbname=utah_test")
QUIET = int(os.environ.get("UTAH_VERIFY_QUIET", "40"))        # secs of no .py change = build paused
INTERVAL = int(os.environ.get("UTAH_VERIFY_INTERVAL", "90"))  # gap between green sweeps
SUITE_TIMEOUT = int(os.environ.get("UTAH_VERIFY_TIMEOUT", "600"))


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


def run_suite() -> tuple[int, float, list[str]]:
    """Run the full suite once. Returns (exit_code, duration_s, failure_lines)."""
    env = dict(os.environ, UTAH_TEST_DSN=TEST_DSN)
    start = time.time()
    try:
        proc = subprocess.run(
            [PY, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--tb=line", "-rf"],
            cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=SUITE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return 124, round(time.time() - start, 1), [f"suite timed out after {SUITE_TIMEOUT}s"]
    except OSError as exc:
        return 125, round(time.time() - start, 1), [f"could not run pytest: {exc}"]
    out = (proc.stdout or "") + (proc.stderr or "")
    noise = ("onnxruntime", "coreml", "context leak")
    fails = [
        ln for ln in out.splitlines()
        if (ln.startswith("FAILED") or "ERROR collecting" in ln
            or "Interrupted" in ln or ln.startswith("ERROR "))
        and not any(n in ln.lower() for n in noise)
    ]
    return proc.returncode, round(time.time() - start, 1), fails[:30]


def verify_once() -> dict:
    rc, dur, fails = run_suite()
    return {
        "ok": rc == 0,
        "state": "green" if rc == 0 else "red",
        "exit_code": rc,
        "duration_s": dur,
        "pyfiles": _pyfiles(),
        "failures": fails,
        "ts": time.time(),
    }


def loop() -> None:
    while True:
        # 1) wait for the builder to pause (up to ~4 min); sample anyway if relentless
        waited = 0
        while _changed_within(QUIET) and waited < 240:
            _write_status({"ok": None, "state": "build_active", "pyfiles": _pyfiles(), "ts": time.time()})
            time.sleep(15)
            waited += 15

        res = verify_once()
        # 2) triple-confirm a red so a moving cross-file write race can't false-alarm
        if not res["ok"]:
            time.sleep(25)
            r2 = verify_once()
            if r2["ok"]:
                r2["note"] = "first run red, cleared on re-run (build-write race)"
                res = r2
            else:
                time.sleep(25)
                r3 = verify_once()
                if r3["ok"]:
                    r3["note"] = "cleared on 3rd run (build-write race)"
                    res = r3
                else:
                    r3["confirmed"] = True
                    r3["note"] = "RED confirmed across 3 runs (~50s) — real regression"
                    res = r3
        _write_status(res)
        time.sleep(INTERVAL)


def main() -> None:
    if "--loop" in sys.argv:
        loop()
        return
    res = verify_once()
    _write_status(res)
    print(json.dumps(res, indent=2))
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
