"""Diff-capture probe marker — an inert target file for selfcode probe runs.

The selfcode pipeline detects what a coding run produced via
``utah/selfcode.py::_real_changed_files`` (tracked diff vs HEAD + new untracked
files). Proving that capture path works end-to-end needs a file the probe can
touch without risking real behavior — that file is this one. It is deliberately
empty of code: nothing imports it, it has no runtime effect, and a probe edit
here is always Tier-A (harmless), so the change-detection machinery can be
exercised on a live repo without touching functional modules.

Keep it import-safe and side-effect-free. Do not delete it as junk: its whole
job is to exist as a stable, documented place for diff-capture probes to land.

The only statements permitted besides this docstring are the inert
``__version__`` string constant and the empty ``__all__`` list below
(asserted by tests/test_probe_marker.py).
"""

__version__ = "1.0.0"

__all__ = []
