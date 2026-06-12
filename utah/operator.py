"""Ace operator — self-heal what we can; page Michael only when a human must be at the screen.

Michael's expectation: Ace is the other half — fixes breakages, hydrates creds from what
we already have, restarts dead substrate, and only interrupts for 2FA/OAuth screens Ace
cannot complete alone. Never hands Michael JSON files.

Runs every foundation cycle and via ``com.utah.operator`` (every 5 min). Never raises.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from utah import auth_repair, config, failures
from utah.daemon import runtime

log = logging.getLogger("utah.operator")

STATUS_PATH = runtime.RUN_DIR / "operator.json"  # default; run() uses runtime.RUN_DIR live
_HUMAN_DEDUP_S = 3600  # one page per issue class per hour

#: Known auto-repair URLs Ace opens when human must click (2FA / app-password).
_REPAIR_URLS = {
    "gmail_app_password": "https://myaccount.google.com/apppasswords",
    "google_oauth": "https://accounts.google.com/o/oauth2/v2/auth",
}


def _run(cmd: list[str], *, timeout: float = 30.0) -> tuple[int, str]:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        out = (res.stdout or res.stderr or "").strip()[:300]
        return res.returncode, out
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)[:200]


def _kickstart(label: str) -> dict:
    uid = os.getuid()
    rc, out = _run(["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"])
    return {"action": f"kickstart {label}", "ok": rc == 0, "detail": out}


def _open_url(url: str) -> bool:
    # `-g` opens in the BACKGROUND so a repair/2FA prompt never steals Michael's focus
    # or yanks the cursor — he finds the tab when ready, the sweep never grabs it.
    try:
        subprocess.run(["open", "-g", url], check=False, timeout=5)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def _human_recently_paged(issue: str, now: float, seen_path: Path) -> bool:
    """True when *issue* was paged inside the dedup window. FILE-backed — the sweep is
    a fresh launchd process every 5 minutes, so in-memory dedup would re-page each run
    (the alerts page-storm lesson). Unreadable state never blocks a page."""
    try:
        seen = json.loads(seen_path.read_text(encoding="utf-8"))
        if issue not in seen:
            return False
        return (now - float(seen[issue])) < _HUMAN_DEDUP_S
    except (OSError, ValueError):
        return False


def _mark_human_paged(issue: str, now: float, seen_path: Path) -> None:
    try:
        seen: dict[str, float] = {}
        try:
            seen = {k: float(v) for k, v in json.loads(seen_path.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError):
            pass
        seen[issue] = now
        seen_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = seen_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(seen) + "\n", encoding="utf-8")
        tmp.replace(seen_path)
    except OSError as exc:
        log.debug("human-page dedup write failed: %s", exc)


def _notify_human(issue: str, message: str, *, url: str | None = None,
                  push_send=None, now_fn=None, seen_path: Path | None = None) -> None:
    """Page Michael once per issue class per :data:`_HUMAN_DEDUP_S` window (file-backed,
    survives the 5-minute process churn). The failure log records EVERY occurrence —
    only the phone page is deduped. Ace opens the repair URL when we have one."""
    now = (now_fn or time.time)()
    seen_path = seen_path or (runtime.RUN_DIR / "operator_human_seen.json")
    if not _human_recently_paged(issue, now, seen_path):
        try:
            if push_send is None:
                from utah.integrations import pushover
                push_send = pushover.send
            if url:
                _open_url(url)
            push_send(
                message,
                title="Ace needs you at the screen",
                priority=0,
                url=config.DECK_TAILNET_URL,
                url_title="Utah deck",
            )
            _mark_human_paged(issue, now, seen_path)
        except Exception as exc:  # noqa: BLE001
            log.warning("human notify failed (%s): %s", issue, exc)
    failures.record("operator", "needs_human", f"{issue}: {message[:400]}")


def repair_substrate(*, pg_fn=None, sup_fn=None, kickstart_fn=None) -> list[dict]:
    """Restart Postgres / supervisor when down. Returns list of repair attempts."""
    from utah import foundation

    pg_fn = pg_fn or foundation.postgres_ready
    sup_fn = sup_fn or foundation.supervisor_alive
    kick = kickstart_fn or _kickstart
    out: list[dict] = []

    if not pg_fn():
        out.append({"target": "postgres", **kick("com.utah.postgres")})
        # Also run the pg script directly (idempotent).
        script = Path.home() / ".utah" / "bin" / "utah_pg.sh"
        if script.is_file():
            rc, detail = _run(["/bin/bash", str(script)], timeout=45)
            out.append({"target": "postgres_script", "ok": rc == 0, "detail": detail})

    if not sup_fn():
        out.append({"target": "supervisor", **kick("com.utah.supervisor")})

    return out


def repair_tailserve(*, script_fn=None) -> dict:
    script = Path.home() / ".utah" / "bin" / "utah_tailserve.sh"
    if not script.is_file():
        return {"ok": False, "detail": "utah_tailserve.sh missing"}
    if script_fn:
        return script_fn()
    rc, detail = _run(["/bin/bash", str(script)], timeout=20)
    return {"ok": rc == 0, "detail": detail}


def repair_integrations(*, mail_verify=None, notify_fn=None) -> dict[str, Any]:
    """Probe live integrations; auto-fix sync paths; Ace repairs auth itself."""
    from utah import auth_repair
    from utah import mail
    from utah import secrets_sync

    notify = notify_fn or _notify_human
    verify = mail_verify or mail.verify

    report: dict[str, Any] = {
        "secrets": secrets_sync.sync_all(write=True),
        "mail": {},
        "auth_repair": {},
        "human": [],
    }

    def _2fa_only(msg: str) -> None:
        notify("gmail_2fa", msg, url=None)

    report["auth_repair"] = auth_repair.repair_gmail(notify_2fa_fn=_2fa_only)
    mv = verify()
    report["mail"] = mv

    if report["auth_repair"].get("needs_2fa"):
        report["human"].append("gmail_2fa")
    elif not mv.get("ok") and not mv.get("gated"):
        # Ace already opened chrome-ace; only page if repair didn't start
        if report["auth_repair"].get("smtp", {}).get("action") != "opened_app_passwords":
            notify(
                "gmail_smtp",
                f"Ace is repairing Gmail for {config.OWNER_EMAIL} — checking chrome-ace session.",
                url=auth_repair.url_with_login_hint(_REPAIR_URLS["gmail_app_password"]),
            )
            report["human"].append("gmail_smtp")

    return report


def sweep_failures(*, recent_fn=None, limit: int = 30) -> list[dict]:
    """Scan recent failures for patterns Ace should act on (deduped)."""
    recent_fn = recent_fn or failures.recent
    rows = recent_fn(limit)
    seen: set[tuple[str, str]] = set()
    actionable: list[dict] = []
    for row in rows:
        key = (row.source, row.kind)
        if key in seen:
            continue
        seen.add(key)
        if row.kind in ("gated", "send_gated", "auth_check_failed", "send_failed", "cron_gated"):
            actionable.append({"source": row.source, "kind": row.kind, "detail": row.detail[:200]})
    return actionable


def _remember_owner_facts() -> None:
    """Promote owner facts Ace must never re-ask (business address, etc.)."""
    try:
        from utah import memory, profile

        memory.init()
        biz = config.BUSINESS_CREDS
        if biz.is_file():
            import json

            data = json.loads(biz.read_text(encoding="utf-8"))
            addr = (data.get("physical_address") or "").strip()
            if config._canspam_is_real(addr):  # noqa: SLF001
                q = "Michael business CAN-SPAM mailing address"
                if not any("dogwood" in h.content.lower() or addr.lower() in h.content.lower()
                           for h in (memory.recall(q, k=3) or [])):
                    profile.remember_profile(f"Michael Barber business CAN-SPAM mailing address: {addr}")
    except Exception as exc:  # noqa: BLE001
        log.debug("remember_owner_facts: %s", exc)


def ensure_app(*, ensure_fn=None) -> dict:
    """Keep ``~/.utah/Ace.app`` built — the visible app Michael opens to grant macOS TCC.

    Part of substrate repair: every sweep calls :func:`utah.operator_app.ensure`, which
    is cheap on the happy path (``_needs_rebuild`` short-circuits when the bundle is
    current and signed). A failed or impossible build is reported honestly as
    ``ok=False`` — never masked as healthy. Never raises.
    """
    try:
        if ensure_fn is None:
            from utah import operator_app

            ensure_fn = operator_app.ensure
        path = ensure_fn()
        if path is None:
            return {"ok": False, "detail": "Ace.app ensure failed (stub missing, non-mac, or build error)"}
        return {"ok": True, "path": str(path)}
    except Exception as exc:  # noqa: BLE001
        log.debug("ensure_app failed: %s", exc)
        return {"ok": False, "detail": str(exc)[:200]}


def permissions_status(*, read_fn=None) -> dict:
    """Last ``python -m utah.permissions bootstrap`` result (Ace.app double-click writes it).

    Honest ``{"present": False}`` when Michael has never run the bootstrap or the file
    is unreadable — absence is surfaced, never invented. Never raises.
    """
    try:
        from utah import permissions

        if read_fn is None:
            def read_fn() -> str:
                return permissions.STATUS_PATH.read_text(encoding="utf-8")
        data = json.loads(read_fn())
        if not isinstance(data, dict):
            return {"present": False, "ok": False, "detail": "malformed status"}
        return {
            "present": True,
            "ok": bool(data.get("ok")),
            "ts": data.get("ts"),
            "microphone": bool((data.get("microphone") or {}).get("ok")),
            "automation": bool((data.get("automation") or {}).get("ok")),
            "deck": bool((data.get("deck") or {}).get("ok")),
        }
    except FileNotFoundError:
        return {"present": False, "ok": False}
    except Exception as exc:  # noqa: BLE001
        log.debug("permissions status read failed: %s", exc)
        return {"present": False, "ok": False, "detail": str(exc)[:200]}


def _safe_outcome(gate_fn) -> dict:
    """Read the revenue outcome gate; never let a DB hiccup abort the operator sweep."""
    try:
        return gate_fn()
    except Exception as exc:  # noqa: BLE001
        log.debug("operator outcome-gate read failed: %s", exc)
        return {"ok": False, "assessable": False, "reason": f"outcome gate error: {exc}"}


def run(
    *,
    substrate_fn=None,
    integrations_fn=None,
    tailserve_fn=None,
    revenue_fn=None,
    app_fn=None,
    permissions_fn=None,
    write_status: bool = True,
) -> dict:
    """One operator sweep. Never raises."""
    substrate_fn = substrate_fn or repair_substrate
    integrations_fn = integrations_fn or repair_integrations
    tailserve_fn = tailserve_fn or repair_tailserve
    app_fn = app_fn or ensure_app
    permissions_fn = permissions_fn or permissions_status
    if revenue_fn is None:
        from utah import revenue_heal
        revenue_fn = revenue_heal.scan

    _remember_owner_facts()

    from utah import revenue_heal

    payload: dict[str, Any] = {
        "ts": time.time(),
        "substrate": substrate_fn(),
        "tailserve": tailserve_fn(),
        "app": app_fn(),             # Ace.app (permissions app) kept built every sweep
        "integrations": integrations_fn(),
        "revenue": revenue_fn(),     # producer gone dark -> Ace files a self-code repair
        # THE OUTCOME GATE (B12): surfaced every sweep so a $0 system is visibly NOT green,
        # no matter how healthy the substrate is. The post-mortem's rule #1, in the machine.
        "outcome": _safe_outcome(revenue_heal.outcome_gate),
        "failures": sweep_failures(),
        "canspam_ready": config.canspam_configured(),
        # Last Ace.app permission bootstrap (utah.permissions) — honest "absent" until run.
        "permissions": permissions_fn(),
    }
    mail = payload["integrations"].get("mail") or {}
    # Honest health: "ok" means what this sweep checked GENUINELY works. A gated
    # (never-configured) mail integration is intentionally off, not healthy — it is
    # surfaced separately via mail_gated so the deck can tell "working" from "off".
    # The permissions bootstrap is a one-time Michael action; its absence is reported
    # above but does not flip the sweep red.
    payload["mail_gated"] = bool(mail.get("gated"))
    substrate_ok = all(step.get("ok") for step in payload["substrate"])
    payload["ok"] = bool(
        substrate_ok
        and (payload["tailserve"] or {}).get("ok")
        and (payload["app"] or {}).get("ok")
        and mail.get("ok")
    )

    if write_status:
        try:
            path = runtime.RUN_DIR / "operator.json"
            runtime.RUN_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.debug("operator status write failed: %s", exc)

    log.info(
        "operator sweep: ok=%s canspam=%s mail_ok=%s mail_gated=%s app_ok=%s human=%s",
        payload["ok"],
        payload["canspam_ready"],
        mail.get("ok"),
        payload["mail_gated"],
        payload["app"].get("ok"),
        payload["integrations"].get("human"),
    )
    return payload


def main() -> int:
    """CLI / launchd entry: ``python -m utah.operator``."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    result = run()
    print(json.dumps({
        "ok": result.get("ok"),
        "canspam": result.get("canspam_ready"),
        "mail_ok": (result["integrations"].get("mail") or {}).get("ok"),
        "mail_gated": result.get("mail_gated"),
        "app_ok": result.get("app", {}).get("ok"),
        "human": result["integrations"].get("human"),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
