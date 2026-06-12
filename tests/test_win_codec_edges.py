"""WIN codec adversarial edges. The decoder runs on bytes off a socket, so every
malformed/hostile frame must raise WinCodecError — never ValueError leaking out of
numpy, never a UnicodeDecodeError, and NEVER an object-dtype array (object buffers
are raw pointers: encoding them ships garbage addresses, decoding them would be
arbitrary-memory interpretation if numpy allowed it)."""
from __future__ import annotations

import struct

import numpy as np
import pytest

from utah import win


# ── legitimate edges ─────────────────────────────────────────────────────────

def test_scalar_zero_dim_roundtrip():
    arr = np.array(3.5, dtype=np.float64)            # ndim == 0
    name, out = win.decode_array(win.encode_array(arr, name="scalar"))
    assert name == "scalar"
    assert out.shape == () and float(out) == 3.5


def test_empty_array_roundtrip():
    arr = np.array([], dtype=np.int32)
    _, out = win.decode_array(win.encode_array(arr))
    assert out.shape == (0,) and out.dtype == np.int32


def test_unicode_name_roundtrip():
    arr = np.array([1.0])
    name, _ = win.decode_array(win.encode_array(arr, name="tickér-µ"))
    assert name == "tickér-µ"


def test_big_endian_dtype_roundtrip():
    arr = np.arange(4, dtype=">f8")                  # explicit byteorder travels
    _, out = win.decode_array(win.encode_array(arr))
    assert np.array_equal(out, arr)


# ── hostile / malformed frames: always WinCodecError, never a leak ───────────

def _frame(dtype_bytes: bytes, name: bytes, shape: tuple[int, ...], body: bytes) -> bytes:
    head = win.MAGIC + win._PRE.pack(len(dtype_bytes), len(name), len(shape))
    return head + dtype_bytes + name + struct.pack(f">{len(shape)}I", *shape) + body


def test_object_dtype_is_rejected_on_encode():
    arr = np.array([object(), object()], dtype=object)
    with pytest.raises(win.WinCodecError):
        win.encode_array(arr)                        # pointers must never hit the wire


def test_object_dtype_frame_is_rejected_on_decode():
    buf = _frame(b"|O", b"", (1,), b"\x00" * 8)
    with pytest.raises(win.WinCodecError):           # not numpy's ValueError
        win.decode_array(buf)


def test_non_ascii_dtype_bytes_raise_codec_error():
    buf = _frame(b"\xff\xfe", b"", (1,), b"\x00" * 8)
    with pytest.raises(win.WinCodecError):           # not UnicodeDecodeError
        win.decode_array(buf)


def test_bogus_dtype_string_raises_codec_error():
    buf = _frame(b"notadtype", b"", (1,), b"\x00" * 8)
    with pytest.raises(win.WinCodecError):
        win.decode_array(buf)


def test_header_truncated_mid_shape_raises():
    good = win.encode_array(np.arange(6, dtype=np.float64).reshape(2, 3))
    cut = good[: win._FIXED + 3 + 0 + 4]             # dtype + part of one shape u32
    with pytest.raises(win.WinCodecError):
        win.decode_array(cut)


def test_shape_body_mismatch_raises():
    buf = _frame(b"<f8", b"", (1000,), b"\x00" * 16)  # declares 8000 bytes, ships 16
    with pytest.raises(win.WinCodecError):
        win.decode_array(buf)


def test_oversized_name_is_rejected_on_encode():
    with pytest.raises(win.WinCodecError):
        win.encode_array(np.array([1.0]), name="x" * 70_000)   # > u16 name field


def test_every_decode_failure_is_the_single_codec_error_type():
    """Callers catch exactly WinCodecError (fail-loud contract) — any other type
    escaping is a crash in the data server's frame loop."""
    hostile = [
        b"",
        b"WIN1",                                      # magic only
        _frame(b"|O", b"", (2,), b"\x00" * 16),
        _frame(b"\x80abc", b"", (1,), b"\x00" * 8),
        win.encode_array(np.arange(10, dtype=np.float64))[:-3],
    ]
    for buf in hostile:
        with pytest.raises(win.WinCodecError):
            win.decode_array(buf)


def test_is_win_sniff_on_edge_inputs():
    assert win.is_win(win.encode_array(np.array([1.0]))) is True
    assert win.is_win(b"WIN1") is True                # magic-only sniff is cheap, not a parse
    assert win.is_win(b"") is False
    assert win.is_win(b"JSON{}") is False
    assert win.is_win(12345) is False                 # non-buffer input: False, not a raise
