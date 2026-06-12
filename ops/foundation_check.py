#!/usr/bin/env python3
"""Utah foundation probe — substrate health independent of the supervisor.

Runs the bottom-layer checks (Postgres, supervisor pid, daemon ping) and writes
``~/.utah/run/foundation.json``. Like ``ops/verify.py``, this must run even when
the product is broken, so it only imports ``utah.foundation`` (not the daemon).

Three honesty guarantees on top of ``foundation.check()`` (launchd KeepAlive
runs this forever — see ``ops/launchd/com.utah.foundation.plist``):

* **bounded** — ``check()`` runs on a watchdog deadline; a wedged probe (e.g. a
  blocked operator repair) becomes an honest red, never a silently hung job;
* **never-raises** — a crashing check or an unwritable status file is reported
  as red with the reason, and ``--loop`` keeps ticking through it (a dead loop
  means a permanently stale ``foundation.json``, which ``gate_cron`` feeds on);
* **publish-or-red** — if ``foundation.json`` can't be written, the verdict
  flips to red: a green nobody can read is not green.

Usage:
  python ops/foundation_check.py           # one shot; exit 0 green / 1 red
  python ops/foundation_check.py --loop    # every 60s (launchd-friendly)

Knobs: UTAH_HOME, UTAH_FOUNDATION_INTERVAL, UTAH_FOUNDATION_CHECK_TIMEOUT.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

log = logging.getLogger("utah.ops.foundation_check")

HOME = Path(os.environ.get("UTAH_HOME", str(Path.home() / ".utah")))
RUN_DIR = HOME / "run"
STATUS = RUN_DIR / "foundation.json"
INTERVAL = int(os.environ.get("UTAH_FOUNDATION_INTERVAL", "60"))
#: foundation.check() itself bounds every probe (pg_isready 5s, ping 3s) but the
#: red path also runs operator repairs — this is the hard outer deadline.
CHECK_TIMEOUT = float(os.environ.get("UTAH_FOUNDATION_CHECK_TIMEOUT", "180"))


def _write_status(payload: dict) -> None:
    """Atomic status write (mkstemp -> replace). Raises on failure — the caller
    decides what an unpublished verdict means (run_once flips it to red)."""
    RUN_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(RUN_DIR), prefix=".foundation", suffix=".json")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle)
        os.replace(tmp, STATUS)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _red(anomaly: str, error: str) -> dict:
    return {"ok": False, "state": "red", "checks": {},
            "anomalies": [anomaly], "error": error}


def _bounded_check(check_fn: Callable[[], dict], timeout_s: float) -> dict:
    """Run *check_fn* on a daemon thread with a hard deadline. A timeout or a
    crash is an honest red payload, never an exception or a hang."""
    box: dict = {}

    def target() -> None:
        try:
            box["payload"] = check_fn()
        except Exception as exc:  # noqa: BLE001 — probe boundary: report, never propagate
            box["error"] = f"{type(exc).__name__}: {exc}"

    worker = threading.Thread(target=target, daemon=True, name="foundation-check")
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        log.error("foundation.check() exceeded %.0fs — reporting red", timeout_s)
        return _red("probe_timeout",
                    f"foundation.check() still running after {timeout_s:.0f}s")
    if "error" in box:
        log.error("foundation.check() crashed: %s", box["error"])
        return _red("probe_crashed", box["error"])
    payload = box.get("payload")
    if not isinstance(payload, dict):
        return _red("probe_bad_payload",
                    f"check() returned {type(payload).__name__}, expected dict")
    return payload


def run_once(check_fn: Callable[[], dict] | None = None, *,
             timeout_s: float | None = None) -> dict:
    """One bounded probe + atomic publish. Never raises.

    ``ok`` is honest twice over: red when the substrate is red, AND red when the
    snapshot could not be published — ``gate_cron`` reads ``foundation.json``,
    so an unwritable status file would leave every cron gating on stale truth.
    """
    if check_fn is None:
        from utah import foundation

        check_fn = foundation.check
    payload = _bounded_check(check_fn, CHECK_TIMEOUT if timeout_s is None else timeout_s)
    payload["ts"] = time.time()
    try:
        _write_status(payload)
    except (OSError, TypeError, ValueError) as exc:
        log.error("cannot publish foundation.json: %s", exc)
        payload["ok"] = False
        payload["state"] = "red"
        anomalies = payload.get("anomalies")
        if isinstance(anomalies, list):
            anomalies.append("status_write_failed")
        else:
            payload["anomalies"] = ["status_write_failed"]
        payload["write_error"] = f"{type(exc).__name__}: {exc}"
    return payload


def _append_red_log(payload: dict) -> None:
    """Best-effort red breadcrumb for post-mortems; its own failure is logged,
    never allowed to kill the loop."""
    try:
        log_path = HOME / "logs" / "foundation.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%F %T')} red {payload.get('anomalies')}\n")
    except OSError as exc:
        log.error("cannot append foundation.log: %s", exc)


def loop(*, run_fn: Callable[[], dict] | None = None,
         sleep_fn: Callable[[float], None] = time.sleep,
         max_ticks: int | None = None) -> None:
    """Probe forever (or *max_ticks* under test). A crashing tick is logged and
    the loop keeps going — a dead prober is a permanently stale foundation.json."""
    run_fn = run_once if run_fn is None else run_fn
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        ticks += 1
        try:
            payload = run_fn()
            print(json.dumps(payload, default=repr), flush=True)
            if not payload.get("ok"):
                _append_red_log(payload)
        except Exception as exc:  # noqa: BLE001 — loop boundary: log and keep ticking
            log.error("foundation tick failed: %s: %s", type(exc).__name__, exc)
        sleep_fn(INTERVAL)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if "--loop" in args:
        loop()
        return 0
    payload = run_once()
    print(json.dumps(payload, default=repr))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
