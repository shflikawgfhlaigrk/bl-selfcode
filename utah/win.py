"""WIN binary data plane — the zero-copy numeric codec (docs 3 & 8).

Ace's "WIN-B" plane was coded for ``cbor2`` which was never installed, so it
silently fell back to JSON — the fast binary path was vapor. Utah's plane is
real: numeric arrays (ticks ``ts,o,h,l,c,v``; int16 PCM audio; telemetry) are
packed as ``struct`` + raw buffer and decoded **zero-copy** via
``numpy.frombuffer`` (no per-element parse/alloc; exact floats incl NaN/Inf,
which JSON loses). It is **fail-loud**: a malformed frame raises
:class:`WinCodecError` — it never silently degrades to JSON (the cbor2 sin).

Wire frame (after the 5-byte length-prefix header from :mod:`utah.daemon.frame`,
carried as ``KIND_BINARY``)::

    MAGIC "WIN1" | dtype_len:u8 name_len:u16 ndim:u8 | dtype_str | name | shape(u32*ndim) | raw bytes

``encode_array`` copies once to serialize; the win is on the READ side —
``decode_array`` returns a view straight over the received buffer.
"""
from __future__ import annotations

import struct

import numpy as np

from utah import UtahError

MAGIC = b"WIN1"
_PRE = struct.Struct(">BHB")  # dtype_len(1) + name_len(2) + ndim(1)
_FIXED = len(MAGIC) + _PRE.size  # 4 + 4 = 8 bytes before the variable header


class WinCodecError(UtahError):
    """A binary frame was malformed/truncated/unknown — fail loud, never JSON."""


def encode_array(arr, name: str = "") -> bytes:
    """Serialize a numpy array to a WIN binary frame. One copy (serialization);
    the zero-copy win is on :func:`decode_array`.

    ``np.asarray`` (not ``ascontiguousarray``, which silently promotes 0-d scalars
    to 1-d and broke shape-faithful round-trips) + ``tobytes`` (always C-order,
    copies a strided view correctly). Object dtypes are refused outright: their
    buffer is raw POINTERS, so framing one ships meaningless (and address-leaking)
    bytes that can never decode."""
    a = np.asarray(arr)
    if a.dtype.hasobject:
        raise WinCodecError("object dtypes cannot be framed (pointer-bearing, not raw numeric)")
    dts = a.dtype.str.encode("ascii")        # e.g. b'<f8' — carries exact byteorder
    nb = name.encode("utf-8")
    if len(dts) > 0xFF or len(nb) > 0xFFFF or a.ndim > 0xFF:
        raise WinCodecError("array name/dtype/ndim too large to frame")
    shape = struct.pack(f">{a.ndim}I", *a.shape)
    header = MAGIC + _PRE.pack(len(dts), len(nb), a.ndim) + dts + nb + shape
    return header + a.tobytes()


def decode_array(buf) -> tuple[str, np.ndarray]:
    """Parse a WIN frame → ``(name, ndarray)``. The array is a **zero-copy view**
    over *buf* (``np.frombuffer`` with an offset — no slicing, no per-element
    parse). Raises :class:`WinCodecError` on anything malformed."""
    mv = memoryview(buf) if not isinstance(buf, (bytes, bytearray, memoryview)) else buf
    n = len(mv)
    if n < _FIXED or bytes(mv[:len(MAGIC)]) != MAGIC:
        raise WinCodecError("not a WIN frame (bad magic / too short)")
    dtype_len, name_len, ndim = _PRE.unpack(mv[len(MAGIC):_FIXED])
    off = _FIXED
    if n < off + dtype_len + name_len + 4 * ndim:
        raise WinCodecError("WIN frame header truncated")
    dts = bytes(mv[off:off + dtype_len]); off += dtype_len
    name = bytes(mv[off:off + name_len]).decode("utf-8", "replace"); off += name_len
    shape = struct.unpack(f">{ndim}I", mv[off:off + 4 * ndim]); off += 4 * ndim
    # The dtype string is wire data — hostile/garbage bytes raise the SAME codec
    # error as every other malformed frame, never a UnicodeDecodeError/ValueError
    # leaking out of numpy into the data server's frame loop.
    try:
        dt = np.dtype(dts.decode("ascii"))
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise WinCodecError(f"unknown dtype {dts!r}") from exc
    if dt.hasobject:
        # An object buffer would be raw pointers — decoding one is interpreting
        # arbitrary memory addresses. Refuse, mirroring encode_array.
        raise WinCodecError(f"object dtype {dts!r} is not a frameable numeric type")
    count = 1
    for d in shape:
        count *= d
    expected = dt.itemsize * count
    if n - off != expected:
        raise WinCodecError(f"WIN body size {n - off} != expected {expected} (truncated/corrupt)")
    # Zero-copy: frombuffer with an offset views *buf* directly; reshape keeps the view.
    try:
        arr = np.frombuffer(buf, dtype=dt, count=count, offset=off).reshape(shape)
    except ValueError as exc:  # numpy refused the buffer/shape — still a codec error
        raise WinCodecError(f"WIN body does not decode as {dt}{shape}: {exc}") from exc
    return name, arr


def is_win(buf) -> bool:
    """True if *buf* starts with the WIN magic (cheap sniff; no full parse)."""
    try:
        return bytes(memoryview(buf)[:len(MAGIC)]) == MAGIC
    except (TypeError, ValueError):
        return False


__all__ = ["MAGIC", "WinCodecError", "encode_array", "decode_array", "is_win"]
