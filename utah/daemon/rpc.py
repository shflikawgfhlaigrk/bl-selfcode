"""JSON-RPC 2.0 over the control frame — typed, strict, msgspec-fast.

Requests are parsed into a frozen :class:`Request` (a bad envelope raises
:class:`RpcError` with the spec code, never a traceback to the peer). Responses
are built as canonical JSON-RPC objects. Error codes cover the JSON-RPC spec
plus Utah's load/availability/admission rejections so a caller can react
(retry, back off, escalate) instead of guessing.
"""
from __future__ import annotations

import msgspec

from utah import UtahError

# -- error codes -------------------------------------------------------------
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# Utah application codes (-32000 block):
OVERLOADED = -32000      # governor shed this call (load/memory admission)
UNAVAILABLE = -32001     # a live dependency (memory/brain) is down
DENIED = -32002          # admission/policy rejection (e.g. bad source)
SHUTTING_DOWN = -32003   # daemon is draining; not accepting new work
TIMEOUT = -32004         # handler exceeded its deadline


class RpcError(UtahError):
    """A structured JSON-RPC error (carries a code + optional data)."""

    def __init__(self, code: int, message: str, data: object | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class Request(msgspec.Struct, frozen=True):
    """One parsed JSON-RPC request. ``id is None`` ⇒ notification (no reply)."""

    method: str
    params: dict | list | None = None
    id: int | str | None = None
    jsonrpc: str = "2.0"

    @property
    def is_notification(self) -> bool:
        return self.id is None


_decoder = msgspec.json.Decoder(Request)
_encoder = msgspec.json.Encoder()


def parse(data: bytes) -> Request:
    """Parse a control frame payload into a :class:`Request`.

    Raises :class:`RpcError` with ``PARSE_ERROR`` / ``INVALID_REQUEST`` so the
    server replies with a proper JSON-RPC error object.
    """
    try:
        req = _decoder.decode(data)
    except msgspec.DecodeError as exc:
        raise RpcError(PARSE_ERROR, f"parse error: {exc}") from exc
    except msgspec.ValidationError as exc:
        raise RpcError(INVALID_REQUEST, f"invalid request: {exc}") from exc
    if req.jsonrpc != "2.0" or not req.method:
        raise RpcError(INVALID_REQUEST, "invalid request: jsonrpc must be '2.0' with a method")
    return req


def ok(request_id: int | str | None, result: object) -> bytes:
    return _encoder.encode({"jsonrpc": "2.0", "id": request_id, "result": result})


def notify(method: str, params: object) -> bytes:
    """A JSON-RPC notification (no id) — used to push bus events to subscribers."""
    return _encoder.encode({"jsonrpc": "2.0", "method": method, "params": params})


def err(
    request_id: int | str | None, code: int, message: str, data: object | None = None
) -> bytes:
    body: dict = {"code": code, "message": message}
    if data is not None:
        body["data"] = data
    return _encoder.encode({"jsonrpc": "2.0", "id": request_id, "error": body})
