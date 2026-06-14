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

from utah import config, failures
from utah.daemon import runtime

log = logging.getLogger("utah.foundation")

STATUS_PATH = runtime.RUN_DIR / "foundation.json"

PG_ISREADY = "/opt/homebrew/opt/postgresql@17/bin/pg_isready"
PG_HOST = "/tmp"
PG_PORT = "5433"
_UNSET = object()


def postgres_ready(*, isready: str = PG_ISREADY, host: str = PG_HOST, port: str = PG_PORT,
                   timeout_s: float = 5.0, attempts: int = 3, retry_sleep: float = 0.5) -> bool:
    """True when Utah Postgres accepts connections on the /tmp socket.

    Bounded by *timeout_s*: a wedged pg_isready (disk stall, socket black hole)
    reads as down instead of hanging the probe — the cron gate needs an answer.

    CONFIRM BEFORE DECLARING DOWN: a single pg_isready can flap transiently — a
    spawn-storm under high load starves the probe past *timeout_s*, or a momentary
    connection refusal — while Postgres is actually fine. One such reading used to
    flip the whole substrate RED, fire a CRITICAL "Postgres not accepting" alert,
    and kick a needless repair on a healthy cluster. So we retry up to *attempts*
    times (small *retry_sleep* between) and report UP on the first success. A real
    outage refuses fast and fails every attempt — still caught, only ~1s slower;
    a healthy cluster answers on the first try, so the common path adds no latency.
    """
    if not os.path.isfile(isready):
        return False  # missing binary is a permanent, fast 'down' — retrying is pointless
    for attempt in range(max(1, attempts)):
        try:
            res = subprocess.run(
                [isready, "-h", host, "-p", port, "-q"],
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
            if res.returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass  # transient — fall through to a retry rather than declaring down
        if attempt < attempts - 1 and retry_sleep > 0:
            time.sleep(retry_sleep)
    return False


def supervisor_alive(*, pid_path=os.fspath(runtime.RUN_DIR / "utah-sup.pid")) -> bool:
    """True when the supervisor pidfile points at a live process.

    pid <= 0 is rejected outright: ``os.kill(0, 0)`` probes OUR process group and
    ``os.kill(-1, 0)`` probes EVERY process — both would read a zeroed/corrupt
    pidfile as a live supervisor.
    """
    try:
        raw = open(pid_path, encoding="utf-8").read().strip()
        pid = int(raw)
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def daemon_ping(*, ping_fn: Callable[[], bool] | None = None) -> bool:
    """True when the control socket answers ``ping``. Total: a probe that raises
    (socket gone, daemon mid-restart) is the down signal, never an exception."""
    try:
        if ping_fn is not None:
            return bool(ping_fn())
        from utah.daemon import client as ctl

        out = ctl.call_sync("ping", timeout=3.0)
        return bool(out.get("ok") or out.get("pong"))
    except Exception:  # noqa: BLE001 — unreachable is the signal
        return False


def _run_probe(name: str, fn: Callable[[], bool]) -> bool:
    """One substrate probe, totalized: a crashing probe is a red check (the
    check() docstring promises 'never raises'), logged so the crash itself
    is not silent."""
    try:
        return bool(fn())
    except Exception:  # noqa: BLE001 — a broken probe must read as down, not crash the cron
        log.warning("foundation probe %s crashed — treating as down", name, exc_info=True)
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
        "postgres": _run_probe("postgres", postgres_fn),
        "supervisor": _run_probe("supervisor", supervisor_fn),
        "daemon": _run_probe("daemon", ping_fn),
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


#: HEAVY crons (scrapers + the self-coder) are load-governed and serialized across
#: processes; LIGHT senders (outreach/marketer/brief) are exempt — a revenue send must
#: run on its hour even under moderate load. (B2: the cron fleet bypassed the daemon's
#: in-process governor and could co-spike load — the AceOS-killer load storm.)
_HEAVY_CRON_CAPS: frozenset = frozenset({
    "leads", "leads_maps", "probate", "probate_enrich",
    "consolidate", "codeindex", "research",
    # NOT "selfcode": the self-coder has its OWN load-defer (SELFCODE_MAX_LOAD_PER_CORE)
    # and is edge-triggered + single-instance, so the foundation cron-mutex would only
    # double-gate it and couple it to the scrapers.
})

_cron_slot_fh = None  # holds the cross-process flock for THIS process's lifetime


def _is_daemon_process() -> bool:
    """True iff THIS process is the long-lived daemon (so an in-daemon ``run_scheduled``
    via the worker pool never grabs the cron mutex and starves the real crons). A cron is
    a separate short-lived process whose pid != the daemon pidfile's."""
    try:
        return int(runtime.PID_PATH.read_text().strip()) == os.getpid()
    except Exception:  # noqa: BLE001 — no pidfile / unreadable → treat as a standalone cron
        return False


def _acquire_cron_slot() -> bool:
    """Non-blocking cross-process mutex so two HEAVY crons never run at once. Returns True
    if this process holds (or now holds) the slot; False if another cron holds it. The lock
    is a flock held for the process lifetime — auto-released when the cron exits."""
    global _cron_slot_fh
    if _cron_slot_fh is not None:
        return True  # already held by this process
    import fcntl

    try:
        fh = open(runtime.RUN_DIR / "cron.lock", "w")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _cron_slot_fh = fh  # keep the fd alive → lock held until process exit
        return True
    except OSError:
        return False  # another heavy cron holds the slot


def gate_cron(capability: str, *, status: dict | None | object = _UNSET) -> dict | None:
    """Return a skip dict when a cron must NOT run now; ``None`` means proceed.

    Cron entrypoints call this first. It refuses on three grounds:
    1. **substrate not green** — a red Postgres/supervisor/daemon never masquerades as
       ``no_fresh_lead`` or a partial ingest; the skip is explicit;
    2. **load too high** (heavy crons only) — defer instead of piling onto a load storm;
    3. **another heavy cron is running** (heavy crons only) — a cross-process mutex so the
       dozen+ launchd jobs can't co-spike load (B2). Light senders skip 2+3 so a revenue
       send always runs on its hour.
    """
    st = read_status() if status is _UNSET else status
    if not (st and st.get("state") == "green" and st.get("ok")):
        anomalies = list((st or {}).get("anomalies") or ["foundation_unknown"])
        checks = (st or {}).get("checks")
        detail = (f"{capability} cron skipped — substrate not green "
                  f"(anomalies={anomalies}, checks={checks})")
        log.warning(detail)
        failures.record("foundation", "cron_gated", detail)
        return {"status": "substrate_red", "capability": capability,
                "anomalies": anomalies, "checks": checks, "ts": time.time()}

    # Heavy-cron governor (skipped for light senders and for in-daemon calls).
    if capability in _HEAVY_CRON_CAPS and not _is_daemon_process():
        ncpu = os.cpu_count() or 1
        load1 = os.getloadavg()[0]
        if load1 / ncpu > config.CRON_MAX_LOAD_PER_CORE:
            detail = (f"{capability} cron deferred — load {load1:.1f} over "
                      f"{config.CRON_MAX_LOAD_PER_CORE}×{ncpu} cores")
            log.warning(detail)
            return {"status": "load_high", "capability": capability,
                    "load1": load1, "ncpu": ncpu, "ts": time.time()}
        if not _acquire_cron_slot():
            detail = f"{capability} cron deferred — another heavy cron holds the slot"
            log.info(detail)
            return {"status": "cron_busy", "capability": capability, "ts": time.time()}
    return None


__all__ = [
    "STATUS_PATH",
    "check",
    "daemon_ping",
    "gate_cron",
    "postgres_ready",
    "read_status",
    "supervisor_alive",
]
