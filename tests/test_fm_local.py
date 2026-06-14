import pytest

from utah import fm_local


def test_generate_rejects_empty():
    with pytest.raises(ValueError):
        fm_local.generate("   ")


def test_on_device_model_round_trips():
    """Real round-trip against Apple Foundation Models. Skips (not fails) on a machine
    where the on-device model isn't available, so the suite stays green off-device."""
    if not fm_local.available():
        pytest.skip("Foundation Models not available on this machine")
    out = fm_local.generate("Reply with exactly one word: pong")
    assert isinstance(out, str) and out.strip()        # a real, non-empty answer
