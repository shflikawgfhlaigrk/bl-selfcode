"""STT must resolve the Moonshine model from the LOCAL cache — never a network
HEAD to huggingface.co on the mic-muted critical path. A network blip there is a
multi-second *deaf window*; a full outage is dead voice. The guard lives IN CODE
(not just a launchd plist) so it survives `kickstart -k` (which never reapplies
plist env) and ships correctly inside the Sovereign buyer package (no launchd at
all). See utah/voice/stt.py.
"""
from __future__ import annotations

import importlib
import os


def test_importing_stt_forces_hf_offline(monkeypatch):
    # Simulate a process launched WITHOUT the launchd env — the live regression
    # where the supervisor's loaded job def predated the plist's HF_HUB_OFFLINE.
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)

    import utah.voice.stt as stt
    importlib.reload(stt)  # re-run module top-level with the env unset

    # Importing the STT boundary must have forced offline model resolution, so the
    # lazy `import moonshine_onnx` / huggingface_hub never does a network HEAD.
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_offline_default_yields_to_explicit_override(monkeypatch):
    # An operator who deliberately wants a cold download sets HF_HUB_OFFLINE=0;
    # the in-code guard is a default (setdefault), so it must not clobber that.
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)

    import utah.voice.stt as stt
    importlib.reload(stt)

    assert os.environ["HF_HUB_OFFLINE"] == "0"            # override preserved
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"      # unset one still defaulted
