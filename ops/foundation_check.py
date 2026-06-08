#!/usr/bin/env python3
"""Utah foundation probe — substrate health independent of the supervisor.

Runs the bottom-layer checks (Postgres, supervisor pid, daemon ping) and writes
``~/.utah/run/foundation.json``. Like ``ops/verify.py``, this must run even when
the product is broken, so it only imports ``utah.foundation`` (not the daemon).

Usage:
  python ops/foundation_check.py           # one shot; exit 0 green / 1 red
  python ops/foundation_check.py --loop    # every 60s (launchd-friendly)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HOME = Path(os.environ.get("UTAH_HOME", str(Path.home() / ".utah")))
RUN_DIR = HOME / "run"
STATUS = RUN_DIR / "foundation.json"
INTERVAL = int(os.environ.get("UTAH_FOUNDATION_INTERVAL", "60"))


def _write_status(payload: dict) -> None:
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


def run_once() -> dict:
    from utah import foundation

    payload = foundation.check()
    payload["ts"] = time.time()
    _write_status(payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    loop = "--loop" in args
    if loop:
        while True:
            payload = run_once()
            print(json.dumps(payload))
            if not payload.get("ok"):
                log_path = HOME / "logs" / "foundation.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(log_path, "a", encoding="utf-8") as handle:
                    handle.write(f"{time.strftime('%F %T')} red {payload.get('anomalies')}\n")
            time.sleep(INTERVAL)
    payload = run_once()
    print(json.dumps(payload))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
