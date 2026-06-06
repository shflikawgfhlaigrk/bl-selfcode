"""Wire foundation: framing, JSON-RPC envelope, zero-copy binary codecs.

These are pure-serialization tests (no live dependency is stubbed — there is
nothing to stub; bytes in, bytes out). The framing test drives a real in-memory
byte stream to exercise read_exactly / partial-read / oversize paths.
"""
from __future__ import annotations

import anyio
import pytest

from utah.daemon import codec, frame, rpc


class _MemStream:
    """A minimal in-memory ByteStream: hands back queued bytes in chunks."""

    def __init__(self, data: bytes, chunk: int = 3) -> None:
        self._data = data
        self._chunk = chunk
        self._pos = 0

    async def receive(self, max_bytes: int = 65536) -> bytes:
        if self._pos >= len(self._data):
            raise EndOfStream
        n = min(self._chunk, max_bytes, len(self._data) - self._pos)
        out = self._data[self._pos : self._pos + n]
        self._pos += n
        return out

    async def send(self, item: bytes) -> None:  # pragma: no cover
        self._data += item


class EndOfStream(Exception):
    pass


# -- framing -----------------------------------------------------------------

def test_frame_encode_decode_roundtrip():
    payload = b'{"hello":"world"}' * 100
    wire = frame.encode(frame.KIND_JSON, payload)
    assert len(wire) == frame.HEADER_LEN + len(payload)

    async def go():
        kind, got = await frame.read_frame(_MemStream(wire, chunk=7))
        assert kind == frame.KIND_JSON
        assert got == payload

    anyio.run(go)


def test_frame_rejects_oversize_on_encode():
    with pytest.raises(frame.FrameError):
        frame.encode(frame.KIND_JSON, b"x" * 11, max_frame=10)


def test_frame_rejects_oversize_declared_length_before_alloc():
    # header declares a 1 GiB frame; read must refuse before reading payload
    big_header = frame._HEADER.pack(frame.KIND_BINARY, 1 << 30)

    async def go():
        with pytest.raises(frame.FrameError):
            await frame.read_frame(_MemStream(big_header), max_frame=1024)

    anyio.run(go)


def test_frame_incomplete_read_raises():
    async def go():
        with pytest.raises(frame.FrameError):
            await frame.read_frame(_MemStream(b"\x01\x00"))  # truncated header

    anyio.run(go)


# -- JSON-RPC ----------------------------------------------------------------

def test_rpc_parse_request_and_notification():
    req = rpc.parse(b'{"jsonrpc":"2.0","method":"ping","id":7}')
    assert req.method == "ping" and req.id == 7 and not req.is_notification
    note = rpc.parse(b'{"jsonrpc":"2.0","method":"event","params":{"x":1}}')
    assert note.is_notification and note.params == {"x": 1}


def test_rpc_parse_rejects_garbage_and_bad_version():
    with pytest.raises(rpc.RpcError) as e1:
        rpc.parse(b"not json")
    assert e1.value.code == rpc.PARSE_ERROR
    with pytest.raises(rpc.RpcError) as e2:
        rpc.parse(b'{"jsonrpc":"1.0","method":"x"}')
    assert e2.value.code == rpc.INVALID_REQUEST


def test_rpc_ok_and_err_envelopes():
    import json
    okd = json.loads(rpc.ok(7, {"pong": True}))
    assert okd == {"jsonrpc": "2.0", "id": 7, "result": {"pong": True}}
    erd = json.loads(rpc.err(7, rpc.OVERLOADED, "shed", {"load": 9.9}))
    assert erd["error"]["code"] == rpc.OVERLOADED and erd["error"]["data"]["load"] == 9.9


# -- binary data plane (zero-copy) -------------------------------------------

def test_ticks_zero_copy_roundtrip():
    np = pytest.importorskip("numpy")
    rows = [(1.0, 2.0, 3.0, 1.5, 2.5, 1000.0), (2.0, 2.1, 3.1, 1.6, 2.6, 2000.0)]
    buf = codec.encode_ticks(rows)
    arr = codec.decode_ticks(buf)
    assert arr.shape == (2, 6)
    assert arr[1, 5] == 2000.0
    # frombuffer is a view, not a copy → shares memory with the buffer
    assert arr.base is not None


def test_ticks_bad_length_fails_loud():
    with pytest.raises(codec.CodecError):
        codec.decode_ticks(b"\x00\x01\x02")  # not a multiple of 48


def test_pcm_roundtrip():
    pytest.importorskip("numpy")
    buf = codec.encode_pcm([0, 1, -1, 32767, -32768])
    out = codec.decode_pcm(buf)
    assert list(out) == [0, 1, -1, 32767, -32768]
