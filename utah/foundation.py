"""Foundation probe — substrate health for Utah automation.

Before any capability (leads, marketer, trading) can run, three things must hold:
Postgres accepting, the supervisor alive, and the daemon responding to ping.
This module checks that bottom layer, writes ``~/.utah/run/foundation.json``, and
records genuine anomalies to the failure log — never silent degradation.

Independent of ``com.utah.supervisor`` (like ``verify``): it must report when the
substrate is down even if the supervisor is dead.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from typing import Callable

from utah import failures
from utah.daemon import runtime

log = logging.getLogger("utah.foundation")

STATUS_PATH = runtime.RUN_DIR / "foundation.json"

PG_ISREADY = "/opt/homebrew/opt/postgresql@17/bin/pg_isready"
PG_HOST = "/tmp"
PG_PORT = "5433"
_UNSET = object()


def postgres_ready(*, isready: str = PG_ISREADY, host: str = PG_HOST, port: str = PG_PORT) -> bool:
    """True when Utah Postgres accepts connections on the /tmp socket."""
    if not os.path.isfile(isready):
        return False
    try:
        res = subprocess.run(
            [isready, "-h", host, "-p", port, "-q"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return res.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def supervisor_alive(*, pid_path=os.fspath(runtime.RUN_DIR / "utah-sup.pid")) -> bool:
    """True when the supervisor pidfile points at a live process."""
    try:
        raw = open(pid_path, encoding="utf-8").read().strip()
        pid = int(raw)
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def daemon_ping(*, ping_fn: Callable[[], bool] | None = None) -> bool:
    """True when the control socket answers ``ping``."""
    if ping_fn is not None:
        return ping_fn()
    from utah.daemon import client as ctl

    try:
        out = ctl.call_sync("ping", timeout=3.0)
        return bool(out.get("ok") or out.get("pong"))
    except Exception:  # noqa: BLE001 — unreachable is the signal
        return False


def check(
    *,
    postgres_fn: Callable[[], bool] | None = None,
    supervisor_fn: Callable[[], bool] | None = None,
    ping_fn: Callable[[], bool] | None = None,
) -> dict:
    """Snapshot substrate health; record anomalies. Never raises."""
    postgres_fn = postgres_fn or postgres_ready
    supervisor_fn = supervisor_fn or supervisor_alive
    ping_fn = ping_fn or (lambda: daemon_ping(ping_fn=None))

    checks = {
        "postgres": postgres_fn(),
        "supervisor": supervisor_fn(),
        "daemon": ping_fn(),
    }
    anomalies: list[str] = []
    if not checks["postgres"]:
        anomalies.append("postgres_down")
        failures.record(
            "foundation",
            "postgres_down",
            f"Postgres not accepting on {PG_HOST}:{PG_PORT} — ledger/capabilities blocked",
        )
    if not checks["supervisor"]:
        anomalies.append("supervisor_down")
        failures.record(
            "foundation",
            "supervisor_down",
            "com.utah.supervisor pid missing or dead — daemon/web/voice unsupervised",
        )
    if not checks["daemon"]:
        anomalies.append("daemon_unreachable")
        failures.record(
            "foundation",
            "daemon_unreachable",
            "control socket ping failed — automation RPCs blocked",
        )

    ok = not anomalies
    result = {
        "ok": ok,
        "state": "green" if ok else "red",
        "checks": checks,
        "anomalies": anomalies,
    }
    try:
        from utah import secrets_sync

        result["secrets"] = secrets_sync.sync_all(write=True)
    except Exception as exc:  # noqa: BLE001 — probe must never crash
        log.debug("secrets_sync skipped: %s", exc)
    if result.get("ok"):
        try:
            from utah import operator

            result["operator"] = operator.run(write_status=True)
        except Exception as exc:  # noqa: BLE001
            log.debug("operator sweep skipped: %s", exc)
    else:
        # Substrate red — still attempt repair (postgres/supervisor kickstart).
        try:
            from utah import operator

            result["operator"] = {
                "substrate": operator.repair_substrate(),
                "tailserve": operator.repair_tailserve(),
            }
        except Exception as exc:  # noqa: BLE001
            log.debug("operator substrate repair skipped: %s", exc)
    return result


def read_status(*, path=os.fspath(STATUS_PATH)) -> dict | None:
    """Load the last foundation probe snapshot; ``None`` if missing or unreadable."""
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def gate_cron(capability: str, *, status: dict | None | object = _UNSET) -> dict | None:
    """Return a skip dict when substrate is not green; ``None`` means proceed.

    Cron entrypoints call this first so a red Postgres/supervisor/daemon never
    masquerades as ``no_fresh_lead`` or a partial ingest — the skip is explicit.
    """
    st = read_status() if status is _UNSET else status
    if st and st.get("state") == "green" and st.get("ok"):
        return None
    anomalies = list((st or {}).get("anomalies") or ["foundation_unknown"])
    checks = (st or {}).get("checks")
    detail = (
        f"{capability} cron skipped — substrate not green "
        f"(anomalies={anomalies}, checks={checks})"
    )
    log.warning(detail)
    failures.record("foundation", "cron_gated", detail)
    return {
        "status": "substrate_red",
        "capability": capability,
        "anomalies": anomalies,
        "checks": checks,
        "ts": time.time(),
    }


__all__ = [
    "STATUS_PATH",
    "check",
    "daemon_ping",
    "gate_cron",
    "postgres_ready",
    "read_status",
    "supervisor_alive",
]
