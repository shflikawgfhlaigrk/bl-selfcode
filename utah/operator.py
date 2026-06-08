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
    try:
        subprocess.run(["open", url], check=False, timeout=5)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def _notify_human(issue: str, message: str, *, url: str | None = None) -> None:
    """Page Michael once per issue class per hour — Ace opened the repair URL if we have one."""
    key = f"operator/human/{issue}"
    try:
        from utah.integrations import pushover

        if url:
            _open_url(url)
        pushover.send(
            message,
            title="Ace needs you at the screen",
            priority=0,
            url=config.DECK_TAILNET_URL,
            url_title="Utah deck",
        )
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


def run(
    *,
    substrate_fn=None,
    integrations_fn=None,
    tailserve_fn=None,
    revenue_fn=None,
    write_status: bool = True,
) -> dict:
    """One operator sweep. Never raises."""
    substrate_fn = substrate_fn or repair_substrate
    integrations_fn = integrations_fn or repair_integrations
    tailserve_fn = tailserve_fn or repair_tailserve
    if revenue_fn is None:
        from utah import revenue_heal
        revenue_fn = revenue_heal.scan

    _remember_owner_facts()

    payload: dict[str, Any] = {
        "ts": time.time(),
        "substrate": substrate_fn(),
        "tailserve": tailserve_fn(),
        "integrations": integrations_fn(),
        "revenue": revenue_fn(),     # producer gone dark -> Ace files a self-code repair
        "failures": sweep_failures(),
        "canspam_ready": config.canspam_configured(),
    }
    payload["ok"] = payload["integrations"]["mail"].get("ok", False) or payload["integrations"]["mail"].get("gated")

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
        "operator sweep: canspam=%s mail_ok=%s human=%s",
        payload["canspam_ready"],
        payload["integrations"]["mail"].get("ok"),
        payload["integrations"].get("human"),
    )
    return payload


def main() -> int:
    """CLI / launchd entry: ``python -m utah.operator``."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    result = run()
    print(json.dumps({
        "canspam": result.get("canspam_ready"),
        "mail_ok": result["integrations"]["mail"].get("ok"),
        "human": result["integrations"].get("human"),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
