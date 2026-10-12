"""tm_im wire protocol: a hand-written protobuf codec and frame types.

Implements only what this client needs, with no protobuf runtime dependency.
The byte layout follows the tm_im integration docs (``04-realtime.md``) and is
verified in tests against the exact byte strings given there.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from enum import IntEnum


class Cmd(IntEnum):
    UNKNOWN = 0
    AUTH = 1
    AUTH_OK = 2
    PING = 3
    PONG = 4
    SEND = 10
    SEND_ACK = 11
    PUSH = 12
    READ = 13
    SYNC = 14
    SYNC_END = 15
    SYNC_RESP = 16
    KICK = 20
    ERROR = 21


class MsgType(IntEnum):
    TEXT = 1
    IMAGE = 2
    SYSTEM = 3


# ---------------------------------------------------------------------------
# protobuf primitives
# ---------------------------------------------------------------------------
def encode_varint(n: int) -> bytes:
    if n < 0:
        n &= (1 << 64) - 1
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def decode_varint(buf: bytes, i: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if i >= len(buf):
            raise ValueError("varint out of range: truncated data")
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def _tag(field: int, wire: int) -> bytes:
    return encode_varint((field << 3) | wire)


def _enc_uint(field: int, value: int) -> bytes:
    # proto3: default values (0) are not encoded.
    if value == 0:
        return b""
    return _tag(field, 0) + encode_varint(value)


def _enc_bytes(field: int, value: bytes) -> bytes:
    if not value:
        return b""
    return _tag(field, 2) + encode_varint(len(value)) + value


def _enc_str(field: int, value: str) -> bytes:
    return _enc_bytes(field, value.encode("utf-8"))


def iter_fields(buf: bytes) -> Iterator[tuple[int, int, object]]:
    """Yield ``(field_number, wire_type, value)`` for every encoded field."""
    i = 0
    while i < len(buf):
        tag, i = decode_varint(buf, i)
        field, wire = tag >> 3, tag & 0x07
        if wire == 0:
            value, i = decode_varint(buf, i)
            yield field, wire, value
        elif wire == 1:
            if i + 8 > len(buf):
                raise ValueError("I64 field out of range")
            yield field, wire, buf[i : i + 8]
            i += 8
        elif wire == 2:
            length, i = decode_varint(buf, i)
            if i + length > len(buf):
                raise ValueError("LEN field out of range")
            yield field, wire, buf[i : i + length]
            i += length
        elif wire == 5:
            if i + 4 > len(buf):
                raise ValueError("I32 field out of range")
            yield field, wire, buf[i : i + 4]
            i += 4
        else:
            raise ValueError(f"unsupported wire type {wire} (field {field})")


def decode_fields(buf: bytes) -> dict[int, object]:
    """Decode into a mapping. Repeated fields keep only the last value."""
    return {field: value for field, _, value in iter_fields(buf)}


def _repeated_bytes(buf: bytes, field_number: int) -> list[bytes]:
    return [
        value
        for field, wire, value in iter_fields(buf)
        if field == field_number and wire == 2 and isinstance(value, bytes)
    ]


def _as_int(fields: dict[int, object], key: int, default: int = 0) -> int:
    value = fields.get(key, default)
    return int(value) if isinstance(value, int) else default


def _as_str(fields: dict[int, object], key: int, default: str = "") -> str:
    value = fields.get(key)
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return default


# ---------------------------------------------------------------------------
# Frame
# ---------------------------------------------------------------------------
@dataclass
class Frame:
    cmd: int
    req_id: int = 0
    payload: bytes = b""


def encode_frame(cmd: int, req_id: int = 0, payload: bytes = b"") -> bytes:
    return _enc_uint(1, cmd) + _enc_uint(2, req_id) + _enc_bytes(3, payload)


def decode_frame(buf: bytes) -> Frame:
    fields = decode_fields(buf)
    payload = fields.get(3, b"")
    return Frame(
        cmd=_as_int(fields, 1),
        req_id=_as_int(fields, 2),
        payload=payload if isinstance(payload, bytes) else b"",
    )


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
@dataclass
class ImMessage:
    message_id: int
    conv_id: int
    seq: int
    sender_id: int
    msg_type: int
    content_json: str
    reply_to: int = 0
    created_at_ms: int = 0

    def content(self) -> dict:
        try:
            parsed = json.loads(self.content_json) if self.content_json else {}
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def text(self) -> str:
        value = self.content().get("text")
        return value if isinstance(value, str) else ""


def encode_auth_request(token: str, client_version: str = "", device_id: str = "") -> bytes:
    return _enc_str(1, token) + _enc_str(2, client_version) + _enc_str(3, device_id)


@dataclass
class AuthResponse:
    actor_id: int
    handle: str
    actor_type: int
    heartbeat_sec: int


def decode_auth_response(payload: bytes) -> AuthResponse:
    fields = decode_fields(payload)
    return AuthResponse(
        actor_id=_as_int(fields, 1),
        handle=_as_str(fields, 2),
        actor_type=_as_int(fields, 3),
        heartbeat_sec=_as_int(fields, 4),
    )


def encode_send_request(
    conv_id: int,
    client_msg_id: str,
    msg_type: int,
    content_json: str,
    reply_to: int = 0,
) -> bytes:
    return (
        _enc_uint(1, conv_id)
        + _enc_str(2, client_msg_id)
        + _enc_uint(3, msg_type)
        + _enc_str(4, content_json)
        + _enc_uint(5, reply_to)
    )


@dataclass
class SendAck:
    conv_id: int
    seq: int
    message_id: int
    created_at_ms: int
    client_msg_id: str


def decode_send_ack(payload: bytes) -> SendAck:
    fields = decode_fields(payload)
    return SendAck(
        conv_id=_as_int(fields, 1),
        seq=_as_int(fields, 2),
        message_id=_as_int(fields, 3),
        created_at_ms=_as_int(fields, 4),
        client_msg_id=_as_str(fields, 5),
    )


def decode_message(payload: bytes) -> ImMessage:
    fields = decode_fields(payload)
    return ImMessage(
        message_id=_as_int(fields, 1),
        conv_id=_as_int(fields, 2),
        seq=_as_int(fields, 3),
        sender_id=_as_int(fields, 4),
        msg_type=_as_int(fields, 5),
        content_json=_as_str(fields, 6),
        reply_to=_as_int(fields, 7),
        created_at_ms=_as_int(fields, 8),
    )


def decode_push(payload: bytes) -> ImMessage:
    """PushMessage { message = 1 }."""
    message = decode_fields(payload).get(1, b"")
    return decode_message(message if isinstance(message, bytes) else b"")


@dataclass
class SyncResponse:
    messages: list[ImMessage]
    has_more: bool
    truncated: bool


def encode_sync_request(cursors: dict[int, int], limit: int = 200) -> bytes:
    out = bytearray()
    for conv_id, since_seq in cursors.items():
        item = _enc_uint(1, conv_id) + _enc_uint(2, since_seq)
        out += _tag(1, 2) + encode_varint(len(item)) + item
    out += _enc_uint(2, limit)
    return bytes(out)


def decode_sync_response(payload: bytes) -> SyncResponse:
    messages = [decode_message(raw) for raw in _repeated_bytes(payload, 1)]
    fields = decode_fields(payload)
    return SyncResponse(
        messages=messages,
        has_more=bool(_as_int(fields, 2)),
        truncated=bool(_as_int(fields, 3)),
    )


@dataclass
class SyncEnd:
    ok: bool
    message: str
    conv_synced: int


def decode_sync_end(payload: bytes) -> SyncEnd:
    fields = decode_fields(payload)
    return SyncEnd(
        ok=bool(_as_int(fields, 1)),
        message=_as_str(fields, 2),
        conv_synced=_as_int(fields, 3),
    )


@dataclass
class KickNotice:
    reason: int
    detail: str
    retry_after_ms: int


def decode_kick(payload: bytes) -> KickNotice:
    fields = decode_fields(payload)
    return KickNotice(
        reason=_as_int(fields, 1),
        detail=_as_str(fields, 2),
        retry_after_ms=_as_int(fields, 3),
    )


class KickReason(IntEnum):
    OTHER_DEVICE = 1
    SUSPENDED = 2
    SERVER_RESTART = 3
    AUTH_EXPIRED = 4


@dataclass
class ErrorFrame:
    code: int
    message: str
    retryable: bool


def decode_error(payload: bytes) -> ErrorFrame:
    fields = decode_fields(payload)
    return ErrorFrame(
        code=_as_int(fields, 1),
        message=_as_str(fields, 2),
        retryable=bool(_as_int(fields, 3)),
    )


def encode_read_request(conv_id: int, last_read_seq: int) -> bytes:
    return _enc_uint(1, conv_id) + _enc_uint(2, last_read_seq)


__all__ = [
    "AuthResponse",
    "Cmd",
    "ErrorFrame",
    "Frame",
    "ImMessage",
    "KickNotice",
    "KickReason",
    "MsgType",
    "SendAck",
    "SyncEnd",
    "SyncResponse",
    "decode_auth_response",
    "decode_error",
    "decode_fields",
    "decode_frame",
    "decode_kick",
    "decode_message",
    "decode_push",
    "decode_send_ack",
    "decode_sync_end",
    "decode_sync_response",
    "decode_varint",
    "encode_auth_request",
    "encode_frame",
    "encode_read_request",
    "encode_send_request",
    "encode_sync_request",
    "encode_varint",
    "iter_fields",
]
