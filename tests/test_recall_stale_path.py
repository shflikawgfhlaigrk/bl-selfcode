"""_recall_hit_stale: a recalled fact whose only cited source paths are dead must not
ground the brain (decay-protected facts otherwise never age out and keep asserting
since-moved/deleted file locations)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utah.core import _recall_hit_stale  # noqa: E402


def test_live_repo_path_is_not_stale():
    assert _recall_hit_stale("the _build_context helper lives in utah/core.py") is False


def test_dead_repo_path_is_stale():
    assert _recall_hit_stale("the handler moved out of utah/totally_fake_xyz123.py") is True


def test_no_path_cited_is_never_stale():
    assert _recall_hit_stale("Michael prefers terse, action-oriented answers") is False


def test_bare_filename_without_dir_is_not_flagged():
    # no directory separator -> too ambiguous to treat as a path assertion; kept
    assert _recall_hit_stale("see Model.swift for the auth flow") is False


def test_at_least_one_live_path_keeps_the_fact():
    assert _recall_hit_stale("compare utah/nope_xyz999.py against utah/core.py") is False


def test_dead_home_relative_path_is_stale():
    assert _recall_hit_stale("config used to live at ~/.ace/fake_dir_xyz999/settings.py") is True
