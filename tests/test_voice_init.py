"""utah.voice package surface — the init must document the pipeline, expose the
real submodules via ``__all__``, and stay LIGHT: importing ``utah.voice`` must not
drag in numpy/onnx/audio deps (the web layer imports ``utah.voice.state`` on every
/voice request; a heavy package import would tax the deck for no reason)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import utah.voice as voice_pkg

REPO = Path(__file__).resolve().parents[1]


def test_all_lists_real_submodules_on_disk():
    """__all__ is the package's public map — every name must be an actual module
    file, and every module file must be listed (no orphan/ghost entries)."""
    pkg_dir = Path(voice_pkg.__file__).parent
    on_disk = {p.stem for p in pkg_dir.glob("*.py")} - {"__init__"}
    assert set(voice_pkg.__all__) == on_disk


def test_lazy_attribute_access_imports_submodule():
    assert voice_pkg.state.__name__ == "utah.voice.state"
    assert voice_pkg.liveness.__name__ == "utah.voice.liveness"


def test_unknown_attribute_raises_attribute_error():
    import pytest

    with pytest.raises(AttributeError):
        voice_pkg.not_a_module  # noqa: B018


def test_package_import_is_light():
    """Importing the package alone must not import the heavy audio stack — run in
    a fresh interpreter so this test is immune to import order in the suite."""
    probe = (
        "import sys; import utah.voice; "
        "heavy = [m for m in ('numpy', 'sounddevice', 'onnxruntime', "
        "'utah.voice.loop', 'utah.voice.oww', 'utah.voice.agent') if m in sys.modules]; "
        "print(','.join(heavy))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120,
        cwd=str(REPO),
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == ""  # nothing heavy was pulled in
