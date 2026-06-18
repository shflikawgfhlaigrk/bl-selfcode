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
from typing import Callable, Protocol

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

#: whisper.cpp cold-start race: whisper-server binds its TCP socket BEFORE the model
#: finishes loading, so ``_ready`` (a socket-accept poll) returns True while the first
#: ``/inference`` POST can still be refused/reset for ~1-2s. That refusal is TRANSIENT —
#: on a freshly spawned server, retry it a few times with a short backoff instead of
#: killing the server and dead-zoning for the whole respawn cooldown (the 2026-06-18
#: "first 'hey ace' after the mlx→whisper.cpp switch comes back empty, then 20s deaf"
#: race). A WARM server gets exactly one shot — a refusal there is a real fault.
_WHISPERCPP_COLD_RETRIES = int(os.environ.get("UTAH_WHISPERCPP_COLD_RETRIES", "4"))
_WHISPERCPP_COLD_BACKOFF_S = float(os.environ.get("UTAH_WHISPERCPP_COLD_BACKOFF_S", "0.5"))


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
        """Ready = the model is LOADED and ``/inference`` actually answers — NOT just that
        the socket accepts. whisper-server binds its port in ~0.1s but takes SECONDS to
        load the model (measured ~7.6s for small.en), so a socket-accept check returns True
        far too early and the first real POST races the load → refused/empty → the
        cold-start deafness + 20s cooldown that left commands empty after every (re)boot
        (live 2026-06-18). Probe the REAL endpoint with a tiny silent clip until it
        answers, bounded by the boot budget. Only paid on a cold spawn (a warm server skips
        this), so the model-load cost lands here at boot, not on Michael's first command."""
        import tempfile
        import wave

        deadline = time.monotonic() + (timeout or config.STT_WORKER_BOOT_S)
        fd, probe = tempfile.mkstemp(suffix=".wav", prefix="utah_wcpp_ready_")
        os.close(fd)
        try:
            with wave.open(probe, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(b"\x00\x00" * 1600)   # 0.1s of silence — a cheap load probe
            url = f"http://127.0.0.1:{self._port}/inference"
            while time.monotonic() < deadline:
                try:
                    self._post(url, probe, 5.0)
                    return True          # /inference answered → model loaded → truly ready
                except Exception:        # noqa: BLE001 — refused/loading; keep polling
                    time.sleep(0.3)
            return False
        finally:
            try:
                os.remove(probe)
            except OSError:
                pass

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

    def _post_resilient(self, url: str, wav_path: str, timeout: float, *, cold: bool) -> str:
        """POST the wav, riding through the cold-start race. On a freshly spawned (``cold``)
        server a connection refused/reset means the model is still loading — retry with a
        short backoff rather than treating it as a wedge. A warm server gets one shot. An
        HTTP *status* error is a real server response (not a race), so it never retries."""
        import urllib.error

        attempts = _WHISPERCPP_COLD_RETRIES if cold else 1
        for i in range(attempts):
            try:
                return self._post(url, wav_path, timeout)
            except urllib.error.HTTPError:
                raise  # the server responded (with an error) — real, not a cold-start race
            except (urllib.error.URLError, ConnectionError, OSError) as exc:
                if i + 1 >= attempts:
                    raise
                log.debug("whisper.cpp cold-start POST retry %d/%d (%s) — model still loading",
                          i + 1, attempts, exc)
                time.sleep(_WHISPERCPP_COLD_BACKOFF_S)
        return ""  # unreachable (loop either returns or raises) — satisfies the type checker

    def transcribe(self, wav_path: str) -> str:
        with self._lock:
            # In the post-failure cooldown: don't even try (a wedged server stays
            # wedged; the point is to stop feeding it work and load).
            if time.monotonic() < self._cooldown_until:
                return ""
            try:
                cold = not self._alive()
                if cold:
                    self._kill()
                    self._spawn()
                    if not self._ready():
                        raise TimeoutError("whisper-server failed to become ready")
                raw = self._post_resilient(f"http://127.0.0.1:{self._port}/inference", wav_path,
                                           config.STT_HANG_TIMEOUT_S, cold=cold)
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


class AppleSTT:
    """Apple native Speech Recognition CLI bridge (zero API cost, runs on ANE)."""

    def __init__(self, bin_path: str | None = None) -> None:
        from pathlib import Path
        self._bin = bin_path or str(Path(os.environ.get("UTAH_HOME", str(Path.home() / ".utah"))) / "bin/apple_stt")

    def transcribe(self, wav_path: str) -> str:
        if not os.path.exists(self._bin):
            log.warning("apple_stt binary not found: %s", self._bin)
            return ""
        try:
            result = subprocess.run(
                [self._bin, wav_path],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                return clean_transcript(result.stdout.strip())
            log.warning("apple_stt exited %d: %s", result.returncode, result.stderr.strip())
        except Exception as exc:
            log.warning("apple_stt failed: %s", exc)
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


# ── Dead-engine self-heal ───────────────────────────────────────────────────
# STT engine selection used to happen ONCE at boot (memoized in ``_stt``) and was
# never re-evaluated. That is exactly how voice went silently deaf on 2026-06-18:
# the loop booted, picked an engine whose deps were healthy AT BOOT (mlx_whisper
# imported + warmed fine at 03:47), then mlx_whisper became unimportable later in
# the run — and every transcribe quietly returned "" forever (the worker swallows
# ImportError → "", the loop swallows "" → no command). Wake fired, mic heard, and
# the transcript was always empty, with no error surfaced and no recovery.
#
# Fix: when the loop sees the current engine return "" on real, loud, post-wake
# audio repeatedly, it calls :func:`flag_dead`. That marks the engine dead for a
# cooldown and clears the cache so the NEXT :func:`get_stt` rebuilds — and
# :func:`_build_default_stt` SKIPS engines that are flagged dead, falling through to
# the next one that is actually present on disk. A transient wedge recovers after the
# cooldown; a structural death (uninstalled model, missing binary) keeps falling
# through to the working engine each window. Voice can no longer get stuck on a dead
# transcriber.
_dead_until: dict[str, float] = {}
_DEAD_COOLDOWN_S = float(os.environ.get("UTAH_STT_DEAD_COOLDOWN_S", "120"))


def flag_dead(engine_name: str) -> None:
    """Mark *engine_name* dead for a cooldown and force re-selection (clears the cache).
    Called by the voice loop when the current engine transcribes loud post-wake speech
    to "" repeatedly — the engine, not the speaker, is the fault."""
    _dead_until[engine_name] = time.monotonic() + _DEAD_COOLDOWN_S
    log.error("STT engine %s flagged DEAD for %.0fs — will re-select a working engine",
              engine_name, _DEAD_COOLDOWN_S)
    set_stt(None)


def _is_dead(engine_name: str) -> bool:
    return time.monotonic() < _dead_until.get(engine_name, 0.0)


def _ordered_factories() -> list[tuple[str, "Callable[[], STT | None]"]]:
    """(name, factory) pairs best-first, honoring ``STT_ENGINE`` as the primary then
    the rest as fallbacks. Each factory returns ``None`` when that engine is not
    available on this box (binary/model/package absent), so selection skips it."""
    def mk_apple() -> "STT | None":
        return AppleSTT()

    def mk_whispercpp() -> "STT | None":
        if os.path.exists(config.WHISPERCPP_BIN):
            model = _best_whispercpp_model()
            if model:
                return WhisperCppSTT(model=model)
        return None

    def mk_mlx() -> "STT | None":
        # find_spec is a cheap presence check; a present-but-broken mlx that passes
        # here but fails to import is caught at runtime by the loop's dead-flagging
        # (which then skips it on the next rebuild) rather than importing heavy MLX/
        # Metal into the mic process just to probe it.
        return SubprocessSTT() if importlib.util.find_spec("mlx_whisper") is not None else None

    def mk_moonshine() -> "STT | None":
        return MoonshineSTT() if importlib.util.find_spec("moonshine_onnx") is not None else None

    base = [("whispercpp", mk_whispercpp), ("mlx", mk_mlx), ("moonshine", mk_moonshine)]
    primary = {
        "apple": ("apple", mk_apple),
        "moonshine": ("moonshine", mk_moonshine),
        "mlx": ("mlx", mk_mlx),
        "whisper": ("whispercpp", mk_whispercpp),
        "whispercpp": ("whispercpp", mk_whispercpp),
    }.get(config.STT_ENGINE)
    order: list[tuple[str, "Callable[[], STT | None]"]] = []
    if primary:
        order.append(primary)
    for item in base:
        if item[0] not in {n for n, _ in order}:
            order.append(item)
    return order


def _build_default_stt() -> STT:
    """Pick the best AVAILABLE STT engine that is not currently flagged dead. Default
    primary is whisper.cpp small.en (``STT_ENGINE=whisper``, Metal C++ server, owned +
    killable). MLX Whisper (:class:`SubprocessSTT`) and framework-CPU Moonshine are
    fallbacks. A dead-flagged engine (see :func:`flag_dead`) is skipped so a runtime
    engine death can never trap voice on a silent transcriber."""
    first_available: STT | None = None
    tried: list[str] = []
    for name, make in _ordered_factories():
        eng = make()
        if eng is None:
            continue
        # Key dead-flagging on the CLASS name: that is what the loop has cheaply on hand
        # (``type(get_stt()).__name__``) when an engine misbehaves, and each factory
        # yields a distinct class, so it identifies the engine unambiguously. (The factory
        # ``name`` is only the human-readable ordering label.)
        cls = type(eng).__name__
        tried.append(f"{name}/{cls}")
        if first_available is None:
            first_available = eng  # last resort if every available engine is flagged dead
        if _is_dead(cls):
            log.warning("STT engine %s (%s) is flagged dead (cooldown active) — skipping",
                        name, cls)
            continue
        log.info("STT engine selected: %s (%s)", name, cls)
        return eng
    if first_available is not None:
        log.warning("all available STT engines (%s) are flagged dead — using %s anyway",
                    tried, type(first_available).__name__)
        return first_available
    log.critical("no STT engine available on this host — voice cannot transcribe")
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
           "clean_transcript", "get_stt", "set_stt", "transcribe", "flag_dead"]
