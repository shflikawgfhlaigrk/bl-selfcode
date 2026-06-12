"""Codec contract: malformed input ALWAYS raises CodecError — never a raw
numpy ValueError/TypeError (callers catch the typed error; an untyped leak
turns a bad payload into a server crash) — and decode stays zero-copy/read-only."""
from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from utah.daemon import codec


# -- encode_ticks: every malformed input is a typed CodecError ---------------

def test_ragged_rows_raise_codec_error():
    with pytest.raises(codec.CodecError):
        codec.encode_ticks([(1.0, 2.0, 3.0, 4.0, 5.0, 6.0), (1.0, 2.0)])


def test_non_numeric_rows_raise_codec_error():
    with pytest.raises(codec.CodecError):
        codec.encode_ticks([("a", "b", "c", "d", "e", "f")])


def test_flat_sequence_is_rejected_not_misread():
    # A single un-nested tick is shape (6,), not (1, 6) — silently packing it
    # would corrupt every consumer's row math.
    with pytest.raises(codec.CodecError):
        codec.encode_ticks([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])


def test_wrong_width_rows_raise_codec_error():
    with pytest.raises(codec.CodecError):
        codec.encode_ticks([(1.0, 2.0, 3.0)])


# -- empty + roundtrip edges ---------------------------------------------------

def test_empty_ticks_roundtrip():
    assert codec.encode_ticks([]) == b""
    out = codec.decode_ticks(b"")
    assert out.shape == (0, 6)


def test_decoded_ticks_are_read_only_views():
    buf = codec.encode_ticks([(1.0, 2.0, 3.0, 4.0, 5.0, 6.0)])
    arr = codec.decode_ticks(buf)
    assert arr.flags.writeable is False  # docstring promises read-only
    assert arr.base is not None          # and zero-copy (a view, not a copy)


# -- decode: non-buffer input is typed, not a raw TypeError -------------------

def test_decode_ticks_non_bytes_raises_codec_error():
    with pytest.raises(codec.CodecError):
        codec.decode_ticks(12345)  # type: ignore[arg-type]


def test_decode_pcm_non_bytes_raises_codec_error():
    with pytest.raises(codec.CodecError):
        codec.decode_pcm(None)  # type: ignore[arg-type]


# -- encode_pcm ----------------------------------------------------------------

def test_pcm_non_numeric_raises_codec_error():
    with pytest.raises(codec.CodecError):
        codec.encode_pcm(["loud", "noise"])


def test_pcm_empty_roundtrip():
    assert codec.encode_pcm([]) == b""
    assert list(codec.decode_pcm(b"")) == []
