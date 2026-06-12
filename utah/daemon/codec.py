"""Binary data-plane codecs — zero-copy, fail-loud (never silent-JSON).

Ace's "fast binary path" was vapor: the WIN codec required ``cbor2``, which was
never installed, so it silently fell back to JSON and the binary plane never
ran. Utah's rule (8-binary): the data plane is **real or it raises**. The hot
arrays (market ticks, int16 PCM audio) are packed fixed-width structs read back
**zero-copy** via ``numpy.frombuffer`` — no decode, no per-element alloc.

This module is the foundation the WIN feed plugs into; it is exercised now (so
it can never rot to vapor) and carries ``KIND_BINARY`` frames on the same wire
as control.
"""
from __future__ import annotations

from utah import UtahError


class CodecError(UtahError):
    """A binary codec is missing a hard dependency or got malformed bytes."""


#: One market tick: (epoch_seconds, open, high, low, close, volume) — 6×float64.
TICK_FIELDS = ("ts", "open", "high", "low", "close", "volume")
_TICK_WIDTH = 6 * 8  # bytes per tick


def _numpy():
    try:
        import numpy as np  # heavy: import lazily, fail loud
    except ImportError as exc:  # pragma: no cover - numpy is a hard dep here
        raise CodecError("numpy is required for the binary data plane") from exc
    return np


def encode_ticks(rows) -> bytes:
    """Pack an iterable of 6-tuples into a contiguous little-endian float64 buffer.

    Raises :class:`CodecError` on ragged/non-numeric/mis-shaped input — callers
    catch the typed error; a raw numpy ``ValueError`` would leak untyped.
    """
    np = _numpy()
    try:
        arr = np.asarray(list(rows), dtype="<f8")
    except (ValueError, TypeError) as exc:
        raise CodecError(f"ticks not coercible to float64 rows: {exc}") from exc
    if arr.size == 0:
        return b""
    if arr.ndim != 2 or arr.shape[1] != 6:
        raise CodecError(f"ticks must be Nx6 (got shape {arr.shape})")
    return np.ascontiguousarray(arr).tobytes()


def decode_ticks(buf: bytes):
    """Zero-copy view of a tick buffer as an ``(N, 6)`` float64 array.

    Returns a read-only ndarray backed directly by *buf* — no copy, no decode.
    Raises :class:`CodecError` on non-buffer input or a misaligned length.
    """
    np = _numpy()
    try:
        if len(buf) % _TICK_WIDTH != 0:
            raise CodecError(
                f"tick buffer not a multiple of {_TICK_WIDTH} bytes: {len(buf)}"
            )
        return np.frombuffer(buf, dtype="<f8").reshape(-1, 6)
    except TypeError as exc:  # not bytes-like (len() or frombuffer rejected it)
        raise CodecError(f"tick buffer must be bytes-like: {exc}") from exc


def encode_pcm(samples) -> bytes:
    """Pack int16 PCM samples (audio) into a little-endian buffer.

    Raises :class:`CodecError` on non-numeric input (typed, never raw numpy).
    """
    np = _numpy()
    try:
        return np.ascontiguousarray(np.asarray(samples, dtype="<i2")).tobytes()
    except (ValueError, TypeError, OverflowError) as exc:
        raise CodecError(f"pcm not coercible to int16: {exc}") from exc


def decode_pcm(buf: bytes):
    """Zero-copy int16 view of a PCM buffer.

    Raises :class:`CodecError` on non-buffer input or an odd length.
    """
    np = _numpy()
    try:
        if len(buf) % 2 != 0:
            raise CodecError(f"pcm buffer not a multiple of 2 bytes: {len(buf)}")
        return np.frombuffer(buf, dtype="<i2")
    except TypeError as exc:
        raise CodecError(f"pcm buffer must be bytes-like: {exc}") from exc
