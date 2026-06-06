"""``utah`` — the operator CLI over the control socket.

``utah start`` launches the supervisor (which owns + restarts the daemon),
detached, and waits for a real readiness probe. ``stop`` drains via the
supervisor (verified child exit). ``status``/``ping``/``tell``/``agent`` are
thin control-socket calls. Nothing here holds state; the daemon is the truth.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time

from utah.daemon import client as ctl, runtime
from utah.daemon.lifecycle import live_pid
from utah.daemon.supervisor import SUP_PID


def _read_pid(path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _daemon_up(timeout: float = 1.0) -> bool:
    try:
        ctl.call_sync("ping", timeout=timeout)
        return True
    except Exception:
        return False


def cmd_start(_args) -> int:
    if _daemon_up():
        print(f"utah: already running (daemon pid {live_pid()})")
        return 0
    runtime.ensure_runtime()
    out = open(runtime.LOG_DIR / "utah-sup.out", "a")
    subprocess.Popen(
        [sys.executable, "-m", "utah.daemon.supervisor"],
        env={**os.environ}, stdout=out, stderr=out, start_new_session=True,
    )
    deadline = time.monotonic() + 25.0
    while time.monotonic() < deadline:
        if _daemon_up(timeout=1.0):
            print(f"utah: started (daemon pid {live_pid()})")
            return 0
        time.sleep(0.3)
    print("utah: failed to start (see ~/.utah/logs)", file=sys.stderr)
    return 1


def cmd_stop(_args) -> int:
    sup = _read_pid(SUP_PID)
    if _alive(sup):
        os.kill(sup, signal.SIGTERM)  # supervisor drains the daemon then exits
        for _ in range(80):
            if not _daemon_up(timeout=0.5):
                print("utah: stopped")
                return 0
            time.sleep(0.3)
        print("utah: stop requested (still draining)")
        return 0
    if _daemon_up():
        try:
            ctl.call_sync("shutdown", timeout=3.0)
        except Exception:
            pass
        print("utah: daemon stopped")
        return 0
    print("utah: not running")
    return 0


def cmd_restart(args) -> int:
    cmd_stop(args)
    time.sleep(0.6)
    return cmd_start(args)


def cmd_status(_args) -> int:
    try:
        print(json.dumps(ctl.call_sync("status"), indent=2))
        return 0
    except Exception:
        print("utah: not running")
        return 1


def cmd_ping(_args) -> int:
    try:
        print(ctl.call_sync("ping"))
        return 0
    except Exception:
        print("utah: not running")
        return 1


def cmd_tell(args) -> int:
    text = " ".join(args.text).strip()
    if not text:
        print("utah: nothing to tell", file=sys.stderr)
        return 2
    try:
        r = ctl.call_sync("tell", {"text": text}, timeout=180.0)
    except Exception as exc:
        print(f"utah: {exc}", file=sys.stderr)
        return 1
    print(f"[{r['source']}] {r['text']}")
    return 0


def cmd_agent(args) -> int:
    print(ctl.call_sync("agent", {"seconds": args.seconds}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="utah", description="Utah control CLI")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start", help="start the supervised daemon").set_defaults(fn=cmd_start)
    sub.add_parser("stop", help="drain and stop the daemon").set_defaults(fn=cmd_stop)
    sub.add_parser("restart", help="restart the daemon").set_defaults(fn=cmd_restart)
    sub.add_parser("status", help="live daemon status").set_defaults(fn=cmd_status)
    sub.add_parser("ping", help="liveness probe").set_defaults(fn=cmd_ping)
    t = sub.add_parser("tell", help="ask the brain"); t.add_argument("text", nargs="+"); t.set_defaults(fn=cmd_tell)
    a = sub.add_parser("agent", help="run a pool task"); a.add_argument("seconds", nargs="?", type=float, default=0.5); a.set_defaults(fn=cmd_agent)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
