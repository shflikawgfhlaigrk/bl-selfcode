"""Ace permission bootstrap — one visible app run that asks macOS for everything Utah needs.

Michael double-clicks ``~/.utah/Ace.app`` (built by :mod:`utah.operator_app`). This module:

1. Ensures integration flags exist (``macos.json``).
2. Probes the microphone (TCC prompt against ``com.utah.ace``).
3. Exercises Notes / Contacts / notifications (Automation prompts).
4. Restarts the supervisor if the deck is down.
5. Opens the local command deck (+ optional Trading Engine Lab).
6. Writes ``~/.utah/run/permissions.json`` for the operator/deck to read.

Never raises — every probe returns a structured result.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from utah.daemon import runtime

log = logging.getLogger("utah.permissions")

STATUS_PATH = runtime.RUN_DIR / "permissions.json"
DECK_URL = os.environ.get("UTAH_DECK_URL", "http://127.0.0.1:8766/")
LAB_URL = DECK_URL.rstrip("/") + "/?lab=1"
MACOS_FLAG = runtime.UTAH_HOME / "secrets" / "macos.json"
SUPERVISOR_LABEL = "com.utah.supervisor"


def _run(cmd: list[str], *, timeout: float = 20.0) -> tuple[int, str]:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        out = (res.stdout or res.stderr or "").strip()[:400]
        return res.returncode, out
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)[:200]


def ensure_macos_flag() -> dict[str, Any]:
    """Create ``macos.json`` when absent so integrations know automation is intended."""
    try:
        runtime.UTAH_HOME.joinpath("secrets").mkdir(mode=0o700, parents=True, exist_ok=True)
        if not MACOS_FLAG.is_file():
            MACOS_FLAG.write_text("{}\n", encoding="utf-8")
            return {"ok": True, "created": True}
        return {"ok": True, "created": False}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def probe_microphone(*, seconds: float = 0.8) -> dict[str, Any]:
    """Open the mic briefly; non-zero RMS means TCC granted and hardware is live."""
    if sys.platform != "darwin":
        return {"ok": False, "skipped": True, "reason": "not macOS"}
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as exc:
        return {"ok": False, "error": f"missing dependency: {exc}"}

    try:
        frames = int(16_000 * seconds)
        audio = sd.rec(frames, samplerate=16_000, channels=1, dtype="float32")
        sd.wait()
        rms = float(np.sqrt(np.mean(np.square(audio))))
        ok = rms > 1e-4
        return {"ok": ok, "rms": round(rms, 6), "seconds": seconds}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}


def _osascript(script: str) -> tuple[bool, str]:
    rc, out = _run(["osascript", "-e", script], timeout=15.0)
    return rc == 0, out


def probe_automation(*, notify_fn: Callable[[str, str], tuple[bool, str]] | None = None) -> dict[str, Any]:
    """Exercise the AppleScript integrations Utah uses — triggers Automation TCC once."""
    run = notify_fn or _osascript
    probes: dict[str, Any] = {}

    ok, detail = run('tell application "System Events" to get name of first process whose frontmost is true')
    probes["system_events"] = {"ok": ok, "detail": detail[:120]}

    ok, detail = run('tell application "Notes" to get name of every note')
    probes["notes"] = {"ok": ok, "detail": detail[:120] if detail else "empty or denied"}

    ok, detail = run('tell application "Contacts" to get name of first person')
    probes["contacts"] = {"ok": ok, "detail": detail[:120] if detail else "empty or denied"}

    ok, detail = run('display notification "Ace setup — desktop alerts are working." with title "Ace"')
    probes["notification"] = {"ok": ok, "detail": detail[:120]}

    ok, detail = run("set the clipboard to \"ace-setup-probe\"")
    probes["clipboard"] = {"ok": ok, "detail": detail[:120]}

    rc, clip = _run(["pbpaste"], timeout=5.0)
    probes["clipboard_read"] = {"ok": rc == 0 and "ace-setup-probe" in clip, "detail": clip[:40]}

    passed = sum(1 for p in probes.values() if p.get("ok"))
    return {"ok": passed >= 4, "passed": passed, "total": len(probes), "probes": probes}


def probe_deck(*, url: str = DECK_URL, timeout: float = 4.0) -> dict[str, Any]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(512).decode("utf-8", errors="replace")
            return {"ok": resp.status == 200, "status": resp.status, "snippet": body[:80]}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "error": str(exc)[:200]}


def repair_supervisor(*, kickstart_fn=None) -> dict[str, Any]:
    kick = kickstart_fn or (lambda label: _run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"]))
    rc, out = kick(SUPERVISOR_LABEL)
    time.sleep(2.0)
    deck = probe_deck()
    return {"kickstart_ok": rc == 0, "detail": out, "deck_after": deck}


def open_urls(*, deck: bool = True, lab: bool = True) -> dict[str, Any]:
    opened: list[str] = []
    try:
        if deck:
            subprocess.run(["open", DECK_URL], check=False, timeout=5)
            opened.append(DECK_URL)
        if lab:
            subprocess.run(["open", LAB_URL], check=False, timeout=5)
            opened.append(LAB_URL)
        return {"ok": True, "opened": opened}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc), "opened": opened}


def _dialog(lines: list[str]) -> None:
    msg = "\\n".join(lines)[:900]
    script = f'display dialog {json.dumps(msg)} with title "Ace setup" buttons {{"OK"}} default button "OK" with icon note'
    _run(["osascript", "-e", script], timeout=30.0)


def bootstrap(
    *,
    open_browser: bool = True,
    open_lab: bool = True,
    restart_if_down: bool = True,
    show_dialog: bool = True,
    write_status: bool = True,
) -> dict[str, Any]:
    """Run the full permission + deck bootstrap. Safe to call from Ace.app or CLI."""
    runtime.ensure_runtime()

    payload: dict[str, Any] = {
        "ts": time.time(),
        "bundle": "com.utah.ace",
        "macos_flag": ensure_macos_flag(),
        "microphone": probe_microphone(),
        "automation": probe_automation(),
        "deck": probe_deck(),
    }

    if restart_if_down and not payload["deck"].get("ok"):
        payload["supervisor"] = repair_supervisor()
        payload["deck"] = probe_deck()

    if open_browser:
        payload["browser"] = open_urls(deck=True, lab=open_lab)

    mic_ok = payload["microphone"].get("ok")
    auto_ok = payload["automation"].get("ok")
    deck_ok = payload["deck"].get("ok")
    payload["ok"] = bool(mic_ok and auto_ok and deck_ok)

    if write_status:
        try:
            tmp = STATUS_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            tmp.replace(STATUS_PATH)
        except OSError as exc:
            log.warning("permissions status write failed: %s", exc)

    if show_dialog:
        voice_app = runtime.UTAH_HOME / "UtahVoice.app"
        lines = [
            "Ace setup finished.",
            "",
            f"Microphone (Ace): {'OK' if mic_ok else 'NEEDS GRANT — Privacy → Microphone → Ace'}",
            f"Automation: {'OK' if auto_ok else 'Click Allow on prompts, or Privacy → Automation'}",
            f"Command deck: {'UP at ' + DECK_URL if deck_ok else 'still starting — reopen Ace in ~10s'}",
            "",
            "Always-on voice uses Utah Voice (separate mic grant under Privacy → Microphone).",
            f"Utah Voice app: {'present' if voice_app.is_dir() else 'missing — restart supervisor'}",
            "",
            "Trading Engine Lab opens in a second browser tab.",
        ]
        try:
            _dialog(lines)
        except Exception:  # noqa: BLE001
            pass

    log.info(
        "permissions bootstrap: mic=%s auto=%s deck=%s",
        mic_ok,
        auto_ok,
        deck_ok,
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    args = list(argv or sys.argv[1:])
    cmd = (args[0] if args else "bootstrap").lower()
    if cmd in ("bootstrap", "run", "setup"):
        result = bootstrap()
        print(json.dumps({"ok": result.get("ok"), "deck": result.get("deck", {}).get("ok")}))
        return 0 if result.get("ok") else 1
    if cmd == "status":
        if STATUS_PATH.is_file():
            print(STATUS_PATH.read_text(encoding="utf-8"))
            return 0
        print("{}")
        return 1
    print("usage: python -m utah.permissions [bootstrap|status]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
