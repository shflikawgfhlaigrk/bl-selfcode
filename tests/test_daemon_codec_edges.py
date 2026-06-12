"""Codec wire-level edges (complements test_daemon_codec.py): misaligned
buffers are typed errors (a torn frame must never decode into shifted rows),
roundtrips are bit-exact (incl. NaN/Inf — the reason this plane is binary, not
JSON), the wire is pinned little-endian (a big-endian writer would corrupt
every consumer silently), and out-of-range PCM ints fail typed."""
from __future__ import annotations

import math
import struct

import pytest

np = pytest.importorskip("numpy")

from utah.daemon import codec


# -- misaligned buffers: typed, never shifted rows -----------------------------

def test_misaligned_tick_buffer_raises_codec_error():
    good = codec.encode_ticks([(1.0, 2.0, 3.0, 4.0, 5.0, 6.0)])
    with pytest.raises(codec.CodecError):
        codec.decode_ticks(good[:-1])  # torn frame: 47 of 48 bytes


def test_odd_length_pcm_buffer_raises_codec_error():
    with pytest.raises(codec.CodecError):
        codec.decode_pcm(b"\x00\x01\x02")  # 3 bytes can't be int16 samples


# -- bit-exact roundtrips --------------------------------------------------------

def test_tick_roundtrip_is_bit_exact_including_nan_and_inf():
    rows = [
        (1718200000.0, 1.25, 2.5, 0.5, 1.75, 1e9),
        (1718200060.0, float("nan"), float("inf"), -0.0, 3.14159, 0.0),
    ]
    out = codec.decode_ticks(codec.encode_ticks(rows))
    assert out.shape == (2, 6)
    assert out[0].tolist() == list(rows[0])
    assert math.isnan(out[1][1]) and math.isinf(out[1][2])  # JSON loses these
    assert out[1][5] == 0.0


def test_pcm_roundtrip_preserves_full_int16_range():
    samples = [-32768, -1, 0, 1, 32767]
    out = codec.decode_pcm(codec.encode_pcm(samples))
    assert out.tolist() == samples
    assert out.dtype == np.dtype("<i2")


def test_decoded_pcm_is_a_read_only_zero_copy_view():
    buf = codec.encode_pcm([7, -7])
    arr = codec.decode_pcm(buf)
    assert arr.flags.writeable is False
    assert arr.base is not None  # a view over the buffer, not a copy


# -- the wire is little-endian, by contract --------------------------------------

def test_tick_wire_format_is_little_endian_float64():
    buf = codec.encode_ticks([(1.0, 2.0, 3.0, 4.0, 5.0, 6.0)])
    assert len(buf) == 48
    assert struct.unpack("<6d", buf) == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)


def test_pcm_wire_format_is_little_endian_int16():
    assert codec.encode_pcm([1, -2]) == struct.pack("<2h", 1, -2)


def test_big_endian_input_array_is_normalized_to_wire_order():
    """A caller handing a '>f8' array must not leak byte-swapped rows onto the
    wire — encode normalizes to '<f8' regardless of input byte order."""
    arr = np.array([[1.0, 2.0, 3.0, 4.0, 5.0, 6.0]], dtype=">f8")
    assert codec.decode_ticks(codec.encode_ticks(arr)).tolist() == [
        [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    ]


# -- pcm range: typed, never silent wraparound ------------------------------------

def test_pcm_out_of_range_int_raises_codec_error_not_silent_wrap():
    with pytest.raises(codec.CodecError):
        codec.encode_pcm([40000])  # > int16 max: must fail typed, never alias


def test_decode_ticks_accepts_memoryview_input():
    buf = codec.encode_ticks([(1.0, 2.0, 3.0, 4.0, 5.0, 6.0)])
    out = codec.decode_ticks(memoryview(buf))
    assert out.shape == (1, 6) and out[0][0] == 1.0
