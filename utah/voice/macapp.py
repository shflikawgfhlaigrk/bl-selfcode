"""Give the launchd voice loop a *granted* TCC identity for the microphone.

Why this exists (the bug it fixes)
----------------------------------
On macOS the mic decision is made against the process's **responsible
identity**, not the path you typed. The Homebrew *framework* Python always
re-execs into the shared stub ``.../Resources/Python.app/Contents/MacOS/Python``
— a binary with **no** microphone grant. Run from a granted terminal (Claude
Code / Terminal) it inherits that terminal's grant and hears fine; run from
**launchd** (``com.utah.supervisor``) there is no granted responsible process,
so CoreAudio hands back all-zero buffers and the loop logs ``mic_silent``
forever. The pre-existing ``bin/python3.14`` TCC grants are dead weight: the
process re-execs away from that path before it ever opens the mic.

The fix
-------
Wrap the framework app stub in **our own** signed ``.app`` bundle with a stable
bundle id (``com.utah.voice``) and an ``NSMicrophoneUsageDescription``. TCC then
treats the voice process as *that bundle* — a stable subject that (a) can be
granted once via the normal consent prompt and (b) keeps the grant under
launchd and across Python point-upgrades (the dylib changes; our copied stub and
its bundle id do not). This is the same pattern python.org documents for GUI/mic
apps, applied to the daemon's voice child.

A copy of the *app* stub does **not** relaunch (only the ``bin/python`` launcher
trampolines via ``__PYVENV_LAUNCHER__``), so the copy stays put and keeps our
identity — verified empirically before this module was written.

The builder is idempotent and mac-only; callers fall back to ``sys.executable``
when :func:`ensure` returns ``None`` (non-mac, CI, or a build failure) so nothing
but live-mic capability depends on it.
"""
from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from utah.daemon import runtime

log = logging.getLogger("utah.voice.macapp")

BUNDLE_ID = "com.utah.voice"
APP_PATH: Path = runtime.UTAH_HOME / "UtahVoice.app"
EXEC_NAME = "UtahVoice"
MIC_REASON = 'Utah listens for the wake word "ace" and your spoken commands.'

#: codesign talks to the system signing/notarization machinery, which can wedge
#: (stale securityd, slow first-run policy checks). The supervisor calls ensure()
#: while building the voice child's spec — an unbounded hang there stalls the
#: whole fleet bring-up, so every codesign call is time-bounded.
_CODESIGN_TIMEOUT_S = float(os.environ.get("UTAH_VOICE_CODESIGN_TIMEOUT_S", "120"))

#: The app stub sits at this suffix under a framework version directory.
_STUB_SUFFIX = "/Resources/Python.app/Contents/MacOS/Python"


@dataclass
class BundleInfo:
    """Where to launch the voice loop from, and the env it needs."""

    exec_path: str
    python_home: str
    env: dict[str, str] = field(default_factory=dict)


def _real_exec() -> str:
    """Actual on-disk binary backing this interpreter (sees through the relaunch).

    ``sys.executable`` lies — it reports the venv/launcher path even though the
    process re-exec'd into the framework app stub. ``_NSGetExecutablePath`` is
    the truth.
    """
    buf = ctypes.create_string_buffer(4096)
    size = ctypes.c_uint32(4096)
    ctypes.CDLL(None)._NSGetExecutablePath(buf, ctypes.byref(size))
    return os.path.realpath(buf.value.decode())


def _source_stub_and_home() -> tuple[str, str] | None:
    """Locate the framework app stub to copy and the PYTHONHOME it needs.

    Prefers the *running* process's real binary (already the app stub when the
    daemon tree is live), then falls back to ``sys.base_prefix``.
    """
    candidates: list[str] = []
    try:
        candidates.append(_real_exec())
    except Exception:  # noqa: BLE001 - ctypes/_NSGetExecutablePath is best-effort
        pass
    candidates.append(str(Path(sys.base_prefix) / _STUB_SUFFIX.lstrip("/")))

    for cand in candidates:
        if cand.endswith(_STUB_SUFFIX) and os.path.exists(cand):
            return cand, cand[: -len(_STUB_SUFFIX)]
    return None


def _sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _venv_site_packages() -> str | None:
    """site-packages that holds sounddevice/onnxruntime/piper for this interpreter.

    The bundled stub is launched outside the venv launcher, so the venv's
    ``site-packages`` is not auto-added — we must put it on PYTHONPATH.
    """
    for p in sys.path:
        if p.endswith("site-packages") and ".utah/venv" in p:
            return p
    # Derive from sys.prefix (the venv prefix when running through it).
    cand = Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    return str(cand) if cand.exists() else None


def _info_plist() -> dict:
    return {
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": EXEC_NAME,
        "CFBundleDisplayName": "Utah Voice",
        "CFBundleExecutable": EXEC_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "NSMicrophoneUsageDescription": MIC_REASON,
        # Background agent: no Dock icon, but still allowed to show the TCC prompt.
        "LSUIElement": True,
        # Opt OUT of App Nap. An LSUIElement background app is a prime App Nap target:
        # when macOS throttles it, the always-on CoreAudio input callback starves and
        # hands back zero-filled buffers — the loop reads that as mic_silent and the
        # supervisor restart-storms (the recurring voice failure). caffeinate stops
        # SYSTEM idle sleep but never per-process App Nap; only this declarative key
        # (plus the runtime NSProcessInfo assertion in the loop) keeps the audio thread
        # scheduled. Load-bearing — do not remove.
        "NSAppSleepDisabled": True,
        "LSMinimumSystemVersion": "13.0",
    }


def _marker_path() -> Path:
    # OUTSIDE the bundle: any stray file inside Contents/ breaks codesign sealing.
    return runtime.RUN_DIR / "voiceapp.source"


def _needs_rebuild(stub_src: str) -> bool:
    exe = APP_PATH / "Contents" / "MacOS" / EXEC_NAME
    plist = APP_PATH / "Contents" / "Info.plist"
    marker = _marker_path()
    if not (exe.exists() and plist.exists() and marker.exists()):
        return True
    try:
        want = _sha256(stub_src)
        have = marker.read_text().strip()
        if want != have:
            return True
        data = plistlib.loads(plist.read_bytes())
        if data.get("CFBundleIdentifier") != BUNDLE_ID or "NSMicrophoneUsageDescription" not in data:
            return True
        # NOTE: intentionally do NOT rebuild merely because NSAppSleepDisabled is absent.
        # This bundle is adhoc-signed, so its TCC designated requirement is cdhash-only;
        # any rebuild changes the cdhash and ORPHANS the microphone grant (deaf under
        # launchd, no UI to re-consent). New installs get the key from _info_plist(); the
        # App Nap opt-out for an already-granted bundle is applied via its user defaults
        # (see loop._set_app_nap_default) — no bundle change, no cdhash change, no risk.
    except Exception:  # noqa: BLE001
        return True
    # Signature still valid? An unverifiable signature (codesign wedged/missing)
    # reads as stale — rebuild — never an exception into the supervisor.
    try:
        return subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(APP_PATH)],
            capture_output=True, timeout=_CODESIGN_TIMEOUT_S,
        ).returncode != 0
    except (subprocess.TimeoutExpired, OSError):
        return True


def _build(stub_src: str) -> None:
    contents = APP_PATH / "Contents"
    macos = contents / "MacOS"
    if APP_PATH.exists():
        shutil.rmtree(APP_PATH)
    macos.mkdir(parents=True)
    (contents / "Resources").mkdir()

    exe = macos / EXEC_NAME
    shutil.copy2(stub_src, exe)
    os.chmod(exe, 0o755)

    (contents / "Info.plist").write_bytes(plistlib.dumps(_info_plist()))
    (contents / "PkgInfo").write_text("APPL????")
    _marker_path().write_text(_sha256(stub_src))

    # Adhoc signature with a STABLE identifier so the TCC grant survives rebuilds
    # of identical bytes and is attributed to com.utah.voice (not a random cdhash).
    res = subprocess.run(
        ["codesign", "--force", "--sign", "-", "--identifier", BUNDLE_ID, str(APP_PATH)],
        capture_output=True, text=True, timeout=_CODESIGN_TIMEOUT_S,
    )
    if res.returncode != 0:
        raise RuntimeError(f"codesign failed: {res.stderr.strip()}")
    log.info("built signed voice bundle %s (id=%s)", APP_PATH, BUNDLE_ID)


def ensure(force: bool = False) -> BundleInfo | None:
    """Build/refresh ``UtahVoice.app`` and return how to launch the voice loop.

    Returns ``None`` on non-mac or any failure — callers fall back to
    ``sys.executable`` (mic stays deaf under launchd, but nothing else breaks).
    """
    if sys.platform != "darwin":
        return None
    src = _source_stub_and_home()
    if not src:
        log.warning("voice bundle: framework app stub not found; mic fix unavailable")
        return None
    stub_src, python_home = src
    try:
        if force or _needs_rebuild(stub_src):
            _build(stub_src)
    except Exception:  # noqa: BLE001
        log.warning("voice bundle build failed — falling back to sys.executable", exc_info=True)
        return None

    exec_path = str(APP_PATH / "Contents" / "MacOS" / EXEC_NAME)
    env: dict[str, str] = {"PYTHONHOME": python_home}
    parts = [p for p in (_venv_site_packages(), os.environ.get("PYTHONPATH", "")) if p]
    if parts:
        env["PYTHONPATH"] = os.pathsep.join(parts)
    return BundleInfo(exec_path=exec_path, python_home=python_home, env=env)
