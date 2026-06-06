"""Wire framing — the one primitive every socket rides on (control AND data).

A frame is a 5-byte header — ``kind:uint8`` + ``length:uint32`` big-endian —
followed by exactly ``length`` payload bytes. This kills Ace's 64 KiB
``readline`` cap (we length-prefix and ``read_exactly``) and is DoS-hard: an
oversized ``length`` is rejected *before* a single payload byte is allocated.

The same frame carries JSON control (``KIND_JSON``) and zero-copy binary data
(``KIND_BINARY``) — one fabric, fail-loud, ready for terabyte-rate bursts.
Transport-agnostic: works over any object exposing async ``receive()``/``send()``
(anyio ``SocketStream``).
"""
from __future__ import annotations

import struct
from typing import Protocol

from utah import UtahError

#: Default hard cap on a single frame (64 MiB). Rejected before allocation.
MAX_FRAME_BYTES: int = 64 * 1024 * 1024

KIND_JSON: int = 1      # control plane: JSON-RPC
KIND_BINARY: int = 2    # data plane: packed-struct / flatbuffers (zero-copy)

_HEADER = struct.Struct(">BI")  # kind (1) + length (4), big-endian → 5 bytes
HEADER_LEN: int = _HEADER.size


class FrameError(UtahError):
    """A frame was malformed, oversize, or the peer closed mid-frame."""


class ByteStream(Protocol):
    async def receive(self, max_bytes: int = ...) -> bytes: ...
    async def send(self, item: bytes) -> None: ...


def encode(kind: int, payload: bytes, max_frame: int = MAX_FRAME_BYTES) -> bytes:
    """Serialize one frame. Raises :class:`FrameError` if oversize."""
    if not 0 <= kind <= 0xFF:
        raise FrameError(f"frame kind out of range: {kind}")
    n = len(payload)
    if n > max_frame:
        raise FrameError(f"frame too large: {n} > {max_frame}")
    return _HEADER.pack(kind, n) + payload


async def read_exactly(stream: ByteStream, n: int) -> bytes:
    """Read exactly *n* bytes or raise :class:`FrameError` on early EOF."""
    if n == 0:
        return b""
    chunks: list[bytes] = []
    have = 0
    while have < n:
        try:
            chunk = await stream.receive(n - have)
        except (Exception,) as exc:  # anyio.EndOfStream / ClosedResourceError
            if exc.__class__.__name__ in {"EndOfStream", "ClosedResourceError", "BrokenResourceError"}:
                raise FrameError(f"peer closed after {have}/{n} bytes") from exc
            raise
        if not chunk:
            raise FrameError(f"peer closed after {have}/{n} bytes")
        chunks.append(chunk)
        have += len(chunk)
    return b"".join(chunks)


async def read_frame(
    stream: ByteStream, max_frame: int = MAX_FRAME_BYTES
) -> tuple[int, bytes]:
    """Read one whole frame → ``(kind, payload)``. Oversize is rejected before
    the payload is read, so a malicious length never forces a huge allocation."""
    header = await read_exactly(stream, HEADER_LEN)
    kind, length = _HEADER.unpack(header)
    if length > max_frame:
        raise FrameError(f"declared frame length {length} > max {max_frame}")
    payload = await read_exactly(stream, length)
    return kind, payload


async def write_frame(
    stream: ByteStream, kind: int, payload: bytes, max_frame: int = MAX_FRAME_BYTES
) -> None:
    """Encode and send one frame."""
    await stream.send(encode(kind, payload, max_frame))
