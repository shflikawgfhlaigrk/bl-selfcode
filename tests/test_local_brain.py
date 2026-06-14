from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch

from utah import local_brain


@pytest.fixture(autouse=True)
def reset_local_brain():
    local_brain.set_runner(None)
    yield
    local_brain.set_runner(None)


def test_enabled():
    with patch.dict("os.environ", {"UTAH_USE_LOCAL_BRAIN": "1"}):
        assert local_brain.enabled() is True
    with patch.dict("os.environ", {"UTAH_USE_LOCAL_BRAIN": "0"}):
        assert local_brain.enabled() is False
    with patch.dict("os.environ", {}, clear=True):
        assert local_brain.enabled() is False


def test_available():
    with patch.object(Path, "exists", return_value=True), \
         patch.object(Path, "is_file", return_value=True):
        assert local_brain.available() is True

    with patch.object(Path, "exists", return_value=False):
        assert local_brain.available() is False


def test_set_runner_and_ask():
    def mock_runner(prompt, system=None, timeout=15):
        return f"mock:{prompt}:{system}"

    local_brain.set_runner(mock_runner)
    assert local_brain.ask("hello", "sys") == "mock:hello:sys"


def test_ask_or_none():
    # 1. Disabled
    with patch.dict("os.environ", {"UTAH_USE_LOCAL_BRAIN": ""}), \
         patch.object(local_brain, "available", return_value=True):
        assert local_brain.ask_or_none("hello") is None

    # 2. Unavailable
    with patch.dict("os.environ", {"UTAH_USE_LOCAL_BRAIN": "1"}), \
         patch.object(local_brain, "available", return_value=False):
        assert local_brain.ask_or_none("hello") is None

    # 3. Enabled and available but raises exception
    def bad_runner(prompt, system=None, timeout=15):
        raise RuntimeError("failed")

    local_brain.set_runner(bad_runner)
    with patch.dict("os.environ", {"UTAH_USE_LOCAL_BRAIN": "1"}), \
         patch.object(local_brain, "available", return_value=True):
        assert local_brain.ask_or_none("hello") is None

    # 4. Success
    def good_runner(prompt, system=None, timeout=15):
        return "success"

    local_brain.set_runner(good_runner)
    with patch.dict("os.environ", {"UTAH_USE_LOCAL_BRAIN": "1"}), \
         patch.object(local_brain, "available", return_value=True):
        assert local_brain.ask_or_none("hello") == "success"


def test_caption():
    assert local_brain.caption("subject", {}) is None


def test_summarize():
    def mock_runner(prompt, system=None, timeout=15):
        return f"sum:{prompt}:{system}"

    local_brain.set_runner(mock_runner)
    with patch.dict("os.environ", {"UTAH_USE_LOCAL_BRAIN": "1"}), \
         patch.object(local_brain, "available", return_value=True):
        assert local_brain.summarize("text") == "sum:text:Summarize the following in 2-3 sentences. Be factual and specific. No fluff."


def test_extract_facts():
    # Success JSON list
    def good_runner(prompt, system=None, timeout=15):
        return '["fact1", "fact2"]'

    local_brain.set_runner(good_runner)
    with patch.dict("os.environ", {"UTAH_USE_LOCAL_BRAIN": "1"}), \
         patch.object(local_brain, "available", return_value=True):
        assert local_brain.extract_facts("exchange") == ["fact1", "fact2"]

    # Success JSON embedded in markup
    def markup_runner(prompt, system=None, timeout=15):
        return 'Here is the JSON: ["fact1", "fact2"] and some other text'

    local_brain.set_runner(markup_runner)
    with patch.dict("os.environ", {"UTAH_USE_LOCAL_BRAIN": "1"}), \
         patch.object(local_brain, "available", return_value=True):
        assert local_brain.extract_facts("exchange") == ["fact1", "fact2"]

    # Failure malformed JSON
    def bad_json_runner(prompt, system=None, timeout=15):
        return '["fact1", "fact2"'

    local_brain.set_runner(bad_json_runner)
    with patch.dict("os.environ", {"UTAH_USE_LOCAL_BRAIN": "1"}), \
         patch.object(local_brain, "available", return_value=True):
        assert local_brain.extract_facts("exchange") == []

    # Failure not a list
    def dict_runner(prompt, system=None, timeout=15):
        return '{"fact": "value"}'

    local_brain.set_runner(dict_runner)
    with patch.dict("os.environ", {"UTAH_USE_LOCAL_BRAIN": "1"}), \
         patch.object(local_brain, "available", return_value=True):
        assert local_brain.extract_facts("exchange") == []


def test_default_runner_execution():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "output"
        
        # Test basic default runner execution path
        res = local_brain._default_runner("hello", "sys")
        assert res == "output"
        mock_run.assert_called_once()
        
        # Test default runner failure code
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "error"
        with pytest.raises(RuntimeError, match="fm_ask exit 1"):
            local_brain._default_runner("hello")
