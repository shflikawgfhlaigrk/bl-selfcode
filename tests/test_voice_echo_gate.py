"""EchoGate — a thrashing STT must never double-process the same utterance.

2026-06-10 live incident: degraded STT emitted identical transcripts twice; each got
its own brain call and stored turn (doubled replies). The gate drops the in-TTL echo
and nothing else."""
from utah.voice.loop import EchoGate


def test_echo_gate_drops_only_the_in_ttl_duplicate():
    g = EchoGate(ttl_s=20.0)
    assert g.allow("did you miss me?", now=100.0) is True
    assert g.allow("did you miss me?", now=105.0) is False     # echo -> dropped
    assert g.allow("Did You MISS me? ", now=106.0) is False    # case/space-insensitive
    assert g.allow("something else", now=107.0) is True        # different text passes
    assert g.allow("something else", now=140.0) is True        # past TTL -> human repeat


def test_echo_gate_never_blocks_empty_or_first_utterance():
    g = EchoGate()
    assert g.allow("", now=1.0) is True and g.allow("", now=1.5) is True
    assert g.allow("hello", now=2.0) is True
