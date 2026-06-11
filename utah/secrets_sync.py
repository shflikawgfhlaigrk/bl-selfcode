"""Hydrate ``~/.utah/secrets/`` from sources Utah already has — not Michael's job.

Utah already holds Gmail creds, Pushover, Discord, an outreach-config, and OAuth client
ids. This module copies forward what exists, never overwrites a real value with a
placeholder, and reports what's still missing so Ace can ask Michael *once by voice*.

**Isolation (binding rule M):** the RUNTIME never reads ``~/.ace``. The Ace config that
once lived there is forward-migrated into ``~/.utah/secrets/`` exactly ONCE (when the
``~/.utah`` copy is absent); every recurring read after that is from ``~/.utah``. So
"Utah is separate from Ace" is true in the code, not just the docs.

Called from :mod:`utah.foundation` on every probe cycle (cheap, idempotent).
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from utah import config

log = logging.getLogger("utah.secrets_sync")

SECRETS = config.UTAH_HOME / "secrets"
GMAIL = SECRETS / "gmail.json"
BUSINESS = config.BUSINESS_CREDS
GOOGLE = SECRETS / "google.json"

#: Utah-owned config (read every cycle). The Ace originals are migrated here ONCE.
OUTREACH_CFG = SECRETS / "outreach-config.yaml"
OAUTH_CFG = SECRETS / "google-oauth.yaml"
#: Legacy Ace sources — touched ONLY by the one-time migration below, never at runtime.
_OUTREACH_CFG_LEGACY = Path.home() / ".ace" / "outreach-config.yaml"
_OAUTH_CFG_LEGACY = Path.home() / ".ace" / "config.yaml"


def _migrate_legacy_once() -> None:
    """Forward-migrate the Ace config into ``~/.utah`` exactly once (copy iff the target
    is absent and the legacy exists). After this the runtime reads only ``~/.utah`` — the
    recurring ``~/.ace`` dependency the audit flagged is gone. Never raises."""
    for legacy, target in ((_OUTREACH_CFG_LEGACY, OUTREACH_CFG), (_OAUTH_CFG_LEGACY, OAUTH_CFG)):
        try:
            if not target.exists() and legacy.is_file():
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copy2(legacy, target)
                target.chmod(0o600)
                log.info("secrets_sync: migrated %s → %s (one-time; ~/.ace not read again)",
                         legacy, target)
        except OSError as exc:  # noqa: PERF203 — best-effort, two paths
            log.debug("secrets_sync: legacy migrate skipped (%s): %s", legacy, exc)

_PLACEHOLDER = re.compile(
    r"placeholder|replace|\[can-spam|your real|your email|example\.com",
    re.I,
)


def _is_real(value: str | None) -> bool:
    v = (value or "").strip()
    return bool(v) and not _PLACEHOLDER.search(v) and v != config.CANSPAM_PLACEHOLDER


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _parse_yaml_kv(path: Path) -> dict[str, str]:
    """Minimal key: value reader — no PyYAML dep on the hot path."""
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            val = val.strip().strip('"').strip("'")
            out[key.strip()] = val
    except OSError:
        pass
    return out


def sync_business_from_memory() -> str | None:
    """Return CAN-SPAM address from durable memory if Michael told Ace (no write)."""
    try:
        from utah import memory

        memory.init()
    except Exception as exc:  # noqa: BLE001
        # A broken memory backend silently disabled CAN-SPAM address sync, which
        # downstream gates ALL outreach email — worth a trace, not a bare None.
        log.warning("memory unavailable for business-address sync: %s", exc)
        return None
    pat = re.compile(
        r"(\d+\s+[A-Za-z0-9\s.'-]+(?:Rd|Road|St|Street|Ave|Avenue|Dr|Drive|Blvd|Lane|Ln|Way)\.?)",
        re.I,
    )
    for q in (
        "Michael business address CAN-SPAM mailing",
        "business address Michael Barber",
        "28 dogwood",
    ):
        for hit in memory.recall(q, k=8) or []:
            if hit.source not in ("user", "fact", "consolidation"):
                continue
            m = pat.search(hit.content)
            if m:
                addr = m.group(1).strip()
                if _is_real(addr):
                    return addr
    return None


def _fix_ace_outreach_email() -> bool:
    """Rewrite Ace outreach-config if it still has the mthburnsbarber typo."""
    if not OUTREACH_CFG.is_file():
        return False
    try:
        raw = OUTREACH_CFG.read_text(encoding="utf-8")
        if "mthburnsbarber@gmail.com" not in raw.lower():
            return False
        fixed = raw.replace("mthburnsbarber@gmail.com", config.OWNER_EMAIL)
        fixed = fixed.replace("mthburnsbarber@GMAIL.COM", config.OWNER_EMAIL)
        if fixed != raw:
            OUTREACH_CFG.write_text(fixed, encoding="utf-8")
            log.info("secrets_sync: fixed mthburnsbarber typo in %s", OUTREACH_CFG)
            return True
    except OSError:
        pass
    return False


def sync_business(*, write: bool = True) -> dict[str, Any]:
    """Merge business.json from Ace outreach-config + gmail.json + memory."""
    _fix_ace_outreach_email()
    biz = _load_json(BUSINESS)
    ace = _parse_yaml_kv(OUTREACH_CFG) if OUTREACH_CFG.is_file() else {}
    gmail = _load_json(GMAIL)

    updates: list[str] = []
    if _is_real(ace.get("from_name")) and not _is_real(biz.get("from_name")):
        biz["from_name"] = ace["from_name"]
        updates.append("from_name←ace")
    reply = config.normalize_owner_email(
        gmail.get("from") or ace.get("from_address") or config.OWNER_EMAIL
    )
    if _is_real(reply) and config.normalize_owner_email(biz.get("reply_to")) != reply:
        biz["reply_to"] = reply
        updates.append("reply_to←gmail/ace")
    ace_addr = (ace.get("physical_address") or "").strip()
    if _is_real(ace_addr) and not _is_real(biz.get("physical_address")):
        biz["physical_address"] = ace_addr
        updates.append("physical_address←ace")

    if not _is_real(biz.get("physical_address")):
        mem_addr = sync_business_from_memory()
        if mem_addr:
            biz["physical_address"] = mem_addr
            updates.append("physical_address←memory")

    missing = []
    if not _is_real(biz.get("physical_address")):
        missing.append("physical_address")

    if write and updates:
        _save_json(BUSINESS, biz)
        log.info("secrets_sync business: %s", ", ".join(updates))

    return {"updated": updates, "missing": missing, "configured": not missing}


def sync_google(*, write: bool = True) -> dict[str, Any]:
    """Seed google.json OAuth client from Ace config when absent."""
    if GOOGLE.exists() and _load_json(GOOGLE).get("refresh_token"):
        return {"updated": [], "skipped": "google.json already has refresh_token"}
    ace = _parse_yaml_kv(OAUTH_CFG) if OAUTH_CFG.is_file() else {}
    # config.yaml nests under google_oauth — parse crudely from raw file
    client_id = client_secret = ""
    try:
        raw = OAUTH_CFG.read_text(encoding="utf-8")
        m = re.search(r'client_id:\s*"?([^"\n]+)"?', raw)
        if m:
            client_id = m.group(1).strip()
        m = re.search(r'client_secret:\s*"?([^"\n]+)"?', raw)
        if m:
            client_secret = m.group(1).strip()
    except OSError:
        pass
    if not (client_id and client_secret):
        return {"updated": [], "missing": ["google OAuth client (ace config empty)"]}

    g = _load_json(GOOGLE)
    changed = []
    if not g.get("client_id"):
        g["client_id"] = client_id
        changed.append("client_id←ace")
    if not g.get("client_secret"):
        g["client_secret"] = client_secret
        changed.append("client_secret←ace")
    if write and changed:
        _save_json(GOOGLE, g)
        log.info("secrets_sync google: %s", ", ".join(changed))
    if not g.get("refresh_token"):
        return {"updated": changed, "missing": ["google refresh_token (OAuth flow once)"]}
    return {"updated": changed, "missing": []}


def sync_all(*, write: bool = True) -> dict[str, Any]:
    """Idempotent hydrate pass. Never raises."""
    _migrate_legacy_once()   # one-time ~/.ace → ~/.utah; runtime reads only ~/.utah after
    report = {
        "business": sync_business(write=write),
        "google": sync_google(write=write),
        "present": {
            "gmail": GMAIL.is_file(),
            "pushover": (SECRETS / "pushover.json").is_file(),
            "discord": (SECRETS / "discord.json").is_file(),
            "maps": (SECRETS / "maps.json").is_file(),
        },
    }
    still_missing = list(report["business"].get("missing") or [])
    still_missing.extend(report["google"].get("missing") or [])
    report["still_missing"] = still_missing
    report["canspam_ready"] = config.canspam_configured()
    return report


__all__ = ["sync_all", "sync_business", "sync_google"]
