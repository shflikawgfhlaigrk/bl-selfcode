"""Frame hardening — typed fail-loud on every malformed input, exact boundaries.

Complements test_daemon_wire.py (happy-path roundtrip + oversize): these pin
the edge contracts — non-bytes payloads become a typed :class:`FrameError`
(never a raw ``TypeError`` leaking to the server loop), bytes-like variants
(bytearray/memoryview) frame identically, zero-length frames roundtrip, the
size boundary is exact, and every stream-close signal maps to ``FrameError``.
"""
from __future__ import annotations

import anyio
import pytest

from utah.daemon import frame


class _MemStream:
    """In-memory ByteStream that hands back queued bytes in small chunks."""

    def __init__(self, data: bytes, chunk: int = 5) -> None:
        self._data = data
        self._chunk = chunk
        self._pos = 0
        self.sent: list[bytes] = []

    async def receive(self, max_bytes: int = 65536) -> bytes:
        if self._pos >= len(self._data):
            raise anyio.EndOfStream
        n = min(self._chunk, max_bytes, len(self._data) - self._pos)
        out = self._data[self._pos : self._pos + n]
        self._pos += n
        return out

    async def send(self, item: bytes) -> None:
        self.sent.append(item)


class _EmptyChunkStream:
    """A stream whose receive() returns b'' (some transports signal EOF that way)."""

    async def receive(self, max_bytes: int = 65536) -> bytes:
        return b""

    async def send(self, item: bytes) -> None:  # pragma: no cover
        pass


class _EOFErrorStream:
    """A stream that raises EOFError (the multiprocessing-style close signal)."""

    async def receive(self, max_bytes: int = 65536) -> bytes:
        raise EOFError

    async def send(self, item: bytes) -> None:  # pragma: no cover
        pass


# -- encode: typed validation -------------------------------------------------

@pytest.mark.parametrize("kind", [-1, 256, 1000])
def test_encode_rejects_kind_out_of_range(kind):
    with pytest.raises(frame.FrameError):
        frame.encode(kind, b"x")


@pytest.mark.parametrize("payload", ["text", None, 123, [1, 2], {"a": 1}])
def test_encode_rejects_non_bytes_payload_with_typed_error(payload):
    # A str/None payload must be a typed FrameError, never a TypeError that
    # escapes into the server's connection loop as an unhandled crash.
    with pytest.raises(frame.FrameError):
        frame.encode(frame.KIND_JSON, payload)


def test_encode_accepts_bytearray_and_memoryview():
    body = b"\x01\x02\x03\x04"
    expect = frame.encode(frame.KIND_BINARY, body)
    assert frame.encode(frame.KIND_BINARY, bytearray(body)) == expect
    assert frame.encode(frame.KIND_BINARY, memoryview(body)) == expect


def test_encode_exact_boundary_is_allowed_and_one_over_rejected():
    frame.encode(frame.KIND_JSON, b"x" * 10, max_frame=10)  # == max: allowed
    with pytest.raises(frame.FrameError):
        frame.encode(frame.KIND_JSON, b"x" * 11, max_frame=10)


# -- roundtrip edges ----------------------------------------------------------

def test_zero_length_payload_roundtrips():
    wire = frame.encode(frame.KIND_JSON, b"")

    async def go():
        kind, payload = await frame.read_frame(_MemStream(wire))
        assert kind == frame.KIND_JSON and payload == b""

    anyio.run(go)


def test_write_frame_then_read_frame_roundtrips():
    out = _MemStream(b"")

    async def go():
        await frame.write_frame(out, frame.KIND_BINARY, b"abc123")
        wire = b"".join(out.sent)
        kind, payload = await frame.read_frame(_MemStream(wire, chunk=2))
        assert kind == frame.KIND_BINARY and payload == b"abc123"

    anyio.run(go)


def test_read_frame_rejects_length_one_over_max_before_payload():
    header = frame._HEADER.pack(frame.KIND_JSON, 1025)

    async def go():
        with pytest.raises(frame.FrameError):
            await frame.read_frame(_MemStream(header), max_frame=1024)

    anyio.run(go)


def test_read_frame_allows_length_exactly_at_max():
    payload = b"y" * 64
    wire = frame.encode(frame.KIND_JSON, payload, max_frame=64)

    async def go():
        kind, got = await frame.read_frame(_MemStream(wire), max_frame=64)
        assert kind == frame.KIND_JSON and got == payload

    anyio.run(go)


# -- stream-close signals all map to FrameError -------------------------------

def test_empty_chunk_close_maps_to_frame_error():
    async def go():
        with pytest.raises(frame.FrameError):
            await frame.read_frame(_EmptyChunkStream())

    anyio.run(go)


def test_eoferror_close_maps_to_frame_error():
    async def go():
        with pytest.raises(frame.FrameError):
            await frame.read_frame(_EOFErrorStream())

    anyio.run(go)


def test_truncated_payload_after_header_maps_to_frame_error():
    # Header promises 100 bytes; only 3 arrive before EOF.
    wire = frame._HEADER.pack(frame.KIND_JSON, 100) + b"abc"

    async def go():
        with pytest.raises(frame.FrameError):
            await frame.read_frame(_MemStream(wire))

    anyio.run(go)


def test_read_exactly_zero_bytes_is_empty_without_touching_stream():
    async def go():
        # n=0 never calls receive (an _EOFErrorStream would raise if it did).
        assert await frame.read_exactly(_EOFErrorStream(), 0) == b""

    anyio.run(go)
