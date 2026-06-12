"""STT boundary — speech (a WAV file) -> text. Default = Moonshine ONNX
(very-low-latency, on-device). MLX Whisper is the swappable fallback (same
boundary; ``set_stt`` to switch). Injectable for tests; degrades to "" on any
error so the voice loop never crashes on a bad clip.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import select
import signal
import subprocess
import sys
import threading
import time
from typing import Protocol

from utah import config

# ── Offline model resolution (deaf-window fix) ──────────────────────────────
# Moonshine STT runs on the mic-muted critical path. moonshine_onnx resolves its
# weights through huggingface_hub, which fires a network HEAD to huggingface.co on
# EVERY transcribe unless told to stay local — so a network blip is a multi-second
# deaf window and a full outage is dead voice. This MUST be enforced in code, not
# only via a launchd plist: `kickstart -k` never re-reads plist env, and the shipped
# Sovereign buyer package has no launchd at all. setdefault so a deliberate cold
# download (HF_HUB_OFFLINE=0) still wins. Runs before the lazy `import moonshine_onnx`.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

log = logging.getLogger("utah.voice.stt")


class STT(Protocol):
    def transcribe(self, wav_path: str) -> str: ...


class MoonshineSTT:
    """Moonshine ONNX — the default. Very low latency; model auto-downloads once."""

    def __init__(self, model: str | None = None) -> None:
        self._model = model or config.STT_MODEL

    def transcribe(self, wav_path: str) -> str:
        import moonshine_onnx

        out = moonshine_onnx.transcribe(wav_path, self._model)
        if isinstance(out, (list, tuple)):
            return " ".join(str(x) for x in out).strip()
        return str(out).strip()


class MLXWhisperSTT:
    """On-device MLX Whisper (swappable fallback). Model auto-downloads once."""

    def __init__(self, model: str | None = None) -> None:
        self._model = model or config.WHISPER_MODEL

    def transcribe(self, wav_path: str) -> str:
        import mlx_whisper

        result = mlx_whisper.transcribe(wav_path, path_or_hf_repo=self._model)
        # Drop segments Whisper itself flags as probably-not-speech — the silence /
        # fragment hallucination ("Thank you.") that turns a quiet clip into a
        # phantom command. Real speech sits well under the threshold.
        segs = result.get("segments") or []
        if segs:
            kept = [
                s.get("text", "")
                for s in segs
                if float(s.get("no_speech_prob", 0.0)) <= config.STT_MAX_NO_SPEECH
            ]
            return "".join(kept).strip()
        return (result.get("text") or "").strip()


class SubprocessSTT:
    """Runs the real STT engine in a KILLABLE worker process.

    MLX Whisper runs on the Metal GPU, which can DEADLOCK under contention. In
    process that hangs the mic loop's main thread forever — it transcribes while
    holding the echo-mute — so the mic goes permanently deaf until reset (the live
    bug). Isolated here, a hang is recovered by killing the worker after a timeout:
    the loop gets "" and keeps listening, and the next call respawns a fresh worker
    with clean Metal state. The model stays warm across turns, so steady-state
    latency matches in-process. See :mod:`utah.voice.stt_worker`.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        #: Load-aware respawn cooldown. A hang under host overload means the next
        #: MLX/Metal compile will ALSO deadlock — and each respawn spawns a fresh
        #: Metal shader compile, so blind retry becomes a load-storm AMPLIFIER (live
        #: 2026-06-10: STT respawning every few seconds spawned 46 MTLCompilerService
        #: procs while load was 172). After a hang, refuse to respawn for a cooldown
        #: that scales with load — the mic stays alive, just stops feeding the fire.
        self._cooldown_until = 0.0

    def _arm_cooldown(self) -> float:
        """Arm the load-aware respawn cooldown (hang OR error — any reset path):
        base seconds, multiplied while the host is overloaded, because the Metal
        compile a respawn triggers is exactly what deadlocks under load."""
        cd = (config.STT_RESPAWN_COOLDOWN_S
              * (config.STT_RESPAWN_OVERLOAD_MULT if self._overloaded() else 1.0))
        self._cooldown_until = time.monotonic() + cd
        return cd

    def _overloaded(self) -> bool:
        try:
            return os.getloadavg()[0] / (os.cpu_count() or 1) > config.STT_RESPAWN_MAX_LOAD
        except Exception:  # noqa: BLE001
            return False

    def _spawn(self) -> None:
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "utah.voice.stt_worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, bufsize=1, env={**os.environ},
        )
        line = self._readline(config.STT_WORKER_BOOT_S)  # wait for the model to load
        if not line or not json.loads(line).get("ready"):
            raise RuntimeError("STT worker failed to boot")

    def _readline(self, timeout: float) -> str | None:
        if self._proc is None or self._proc.stdout is None:
            return None
        ready, _, _ = select.select([self._proc.stdout], [], [], timeout)
        if not ready:
            return None
        return self._proc.stdout.readline() or None

    def _kill(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
                self._proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                pass
            self._proc = None

    def transcribe(self, wav_path: str) -> str:
        with self._lock:
            # In the respawn cooldown after a hang: skip cheaply (no spawn, no Metal
            # compile) so a wedged worker under host overload stops amplifying load.
            if self._proc is None and time.monotonic() < self._cooldown_until:
                return ""
            try:
                if self._proc is None or self._proc.poll() is not None:
                    self._kill()
                    self._spawn()
                if self._proc is None or self._proc.stdin is None:
                    # explicit (not assert — asserts vanish under -O) so the outer
                    # handler resets + arms the cooldown like any other worker fault
                    raise RuntimeError("STT worker has no stdin after spawn")
                self._proc.stdin.write(wav_path + "\n")
                self._proc.stdin.flush()
                line = self._readline(config.STT_HANG_TIMEOUT_S)
                if line is None:  # worker wedged (MLX/Metal deadlock) — kill + recover
                    cd = self._arm_cooldown()
                    log.error(
                        "STT worker hung >%ss (likely MLX/Metal deadlock) — killing + "
                        "backing off %.0fs (load-aware); the mic stays alive",
                        config.STT_HANG_TIMEOUT_S, cd,
                    )
                    self._kill()
                    return ""
                return (json.loads(line).get("text") or "").strip()
            except Exception as exc:  # noqa: BLE001
                cd = self._arm_cooldown()
                log.warning("STT worker error (%s) — resetting worker, backing off %.0fs",
                            exc, cd)
                self._kill()
                return ""


_NON_SPEECH = ("blank_audio", "blank audio", "silence", "inaudible", "music",
               "wind blowing", "applause", "laughter", "noise", "typing", "clicking")


def clean_transcript(text: str) -> str:
    """Strip whisper.cpp's non-speech annotations — '[BLANK_AUDIO]', '(wind blowing)',
    '[Music]' … — so silence NEVER becomes a question (live 2026-06-10: Ace answered
    '[BLANK_AUDIO]' as if Michael had said it). Bracketed/parenthesised segments are
    annotations, not speech; if nothing real remains, there was no utterance."""
    if not text:
        return ""
    out = re.sub(r"[\[\(][^\]\)]{0,60}[\]\)]", " ", text)
    out = re.sub(r"\s+", " ", out).strip()
    if not re.search(r"[A-Za-z0-9]", out):
        return ""                      # only annotations/punctuation = no utterance
    return out


def _stranger_pids(pgrep_out: str, *, own_pid: int, self_pid: int) -> list[int]:
    """PIDs from ``pgrep -f`` output that are NOT our live child and NOT ourselves —
    the orphans :meth:`WhisperCppSTT._reap_strangers` must kill. Pure; junk tokens
    are ignored (pgrep output is line-oriented pids, but never trust a parser)."""
    pids: list[int] = []
    for tok in pgrep_out.split():
        try:
            pid = int(tok)
        except ValueError:
            continue
        if pid not in (own_pid, self_pid):
            pids.append(pid)
    return pids


class WhisperCppSTT:
    """whisper.cpp server STT — the post-MLX core (Michael 2026-06-10: the recurring
    voice-failure class was ALL MLX/Metal-Python deadlocks under load; 'delete and redo'
    the failing layer). The server (C++ Metal) is OWNED here: spawned once, model loaded
    once, killed on wedge; each utterance is ONE local HTTP call with a hard timeout.
    Same load-aware respawn cooldown as the MLX worker — a wedged server backs off
    instead of amplifying a load storm. Never raises; "" on any failure."""

    def __init__(self, bin_path: str | None = None, model: str | None = None,
                 port: int | None = None) -> None:
        self._bin = bin_path or config.WHISPERCPP_BIN
        self._model = os.path.expanduser(model or config.WHISPERCPP_MODEL)
        self._port = int(port or config.WHISPERCPP_PORT)
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._cooldown_until = 0.0

    # the same circuit-breaker contract as SubprocessSTT
    def _overloaded(self) -> bool:
        try:
            return os.getloadavg()[0] / (os.cpu_count() or 1) > config.STT_RESPAWN_MAX_LOAD
        except Exception:  # noqa: BLE001
            return False

    def _arm_cooldown(self) -> float:
        cd = (config.STT_RESPAWN_COOLDOWN_S
              * (config.STT_RESPAWN_OVERLOAD_MULT if self._overloaded() else 1.0))
        self._cooldown_until = time.monotonic() + cd
        return cd

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _reap_strangers(self) -> None:
        """Kill ORPHANED whisper-servers holding our port before we spawn ours.
        Live 2026-06-10: voice-loop respawns orphaned SIX servers to PPID 1, all
        LISTENing on 8090 at once (SO_REUSEPORT stacks binds) — requests round-robined
        onto stale twins and STT went intermittently deaf while a healthy twin answered
        probes. A spawn must own its port; reaping is best-effort and never raises."""
        try:
            out = subprocess.run(
                ["pgrep", "-f", f"whisper-server.*--port {self._port}"],
                capture_output=True, text=True, timeout=5).stdout
        except Exception:  # noqa: BLE001 — best-effort; spawn proceeds regardless
            return
        own = self._proc.pid if self._proc is not None else -1
        for pid in _stranger_pids(out, own_pid=own, self_pid=os.getpid()):
            try:
                os.kill(pid, signal.SIGKILL)
                log.warning("reaped orphan whisper-server pid=%d (port %d)", pid, self._port)
            except Exception:  # noqa: BLE001 — already gone / not ours to kill
                pass

    def _spawn(self) -> None:
        self._reap_strangers()
        self._proc = subprocess.Popen(
            [self._bin, "-m", self._model, "--host", "127.0.0.1",
             "--port", str(self._port), "-t", "4"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _kill(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
                self._proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                pass
            self._proc = None

    def _ready(self, timeout: float | None = None) -> bool:
        """Poll until the server accepts TCP (model loaded) — bounded by boot budget."""
        import socket

        deadline = time.monotonic() + (timeout or config.STT_WORKER_BOOT_S)
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self._port), timeout=1):
                    return True
            except OSError:
                time.sleep(0.3)
        return False

    @staticmethod
    def _post(url: str, wav_path: str, timeout: float) -> str:
        """One multipart POST of the wav to the server. Injectable for tests."""
        import urllib.request
        import uuid

        boundary = uuid.uuid4().hex
        with open(wav_path, "rb") as f:
            payload = f.read()
        body = (
            (f"--{boundary}\r\n"
             f'Content-Disposition: form-data; name="file"; filename="a.wav"\r\n'
             f"Content-Type: audio/wav\r\n\r\n").encode()
            + payload
            + (f"\r\n--{boundary}\r\n"
               f'Content-Disposition: form-data; name="response_format"\r\n\r\n'
               f"json\r\n--{boundary}--\r\n").encode()
        )
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")

    def transcribe(self, wav_path: str) -> str:
        with self._lock:
            # In the post-failure cooldown: don't even try (a wedged server stays
            # wedged; the point is to stop feeding it work and load).
            if time.monotonic() < self._cooldown_until:
                return ""
            try:
                if not self._alive():
                    self._kill()
                    self._spawn()
                    if not self._ready():
                        raise TimeoutError("whisper-server failed to become ready")
                raw = self._post(f"http://127.0.0.1:{self._port}/inference", wav_path,
                                 config.STT_HANG_TIMEOUT_S)
                try:
                    return clean_transcript((json.loads(raw).get("text") or "").strip())
                except Exception:  # noqa: BLE001 — junk body = no transcript, not a crash
                    return ""
            except Exception as exc:  # noqa: BLE001 — wedge/timeout/refused
                cd = self._arm_cooldown()
                log.warning("whisper.cpp STT error (%s) — killing server, backing off %.0fs",
                            exc, cd)
                self._kill()
                return ""


_stt: STT | None = None


def _best_whispercpp_model() -> str | None:
    """The configured model if present, else the best ggml on disk (small.en beats
    base.en — quality parity with the retired MLX small.en path)."""
    cands = [os.path.expanduser(config.WHISPERCPP_MODEL)]
    root = os.path.expanduser("~/.utah/models/whisper")
    for name in ("ggml-small.en-q5_1.bin", "ggml-small.en.bin",
                 "ggml-base.en-q5_1.bin", "ggml-base.en.bin"):
        cands.append(os.path.join(root, name))
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def _build_default_stt() -> STT:
    """Default = MLX Whisper small.en (accurate) run in a KILLABLE worker process so
    a Metal-GPU deadlock can't deafen the mic (:class:`SubprocessSTT`). The bare
    framework-CPU Moonshine (``STT_ENGINE=moonshine``) is the no-Metal fallback —
    it never hangs but mis-hears real speech ("What's going on?" -> "Blun.")."""
    if config.STT_ENGINE == "moonshine":
        return MoonshineSTT()
    if config.STT_ENGINE in ("whisper", "whispercpp") and os.path.exists(config.WHISPERCPP_BIN):
        model = _best_whispercpp_model()
        if model:
            return WhisperCppSTT(model=model)
    if importlib.util.find_spec("mlx_whisper") is not None:
        return SubprocessSTT()
    log.warning("MLX Whisper not installed — falling back to Moonshine STT")
    return MoonshineSTT()


def get_stt() -> STT:
    global _stt
    if _stt is None:
        _stt = _build_default_stt()
    return _stt


def set_stt(stt: STT | None) -> None:
    global _stt
    _stt = stt


def transcribe(wav_path: str) -> str:
    """Transcribe a WAV to text; "" on any failure (logged, never raised)."""
    try:
        return get_stt().transcribe(wav_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("STT failed (%s): %s", wav_path, exc)
        return ""


__all__ = ["STT", "MoonshineSTT", "MLXWhisperSTT", "SubprocessSTT", "WhisperCppSTT",
           "clean_transcript", "get_stt", "set_stt", "transcribe"]
