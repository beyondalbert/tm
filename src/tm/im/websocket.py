"""Minimal WebSocket client (RFC 6455) over the standard library.

Only what the tm_im realtime connection needs: a binary-message connection with
client-side masking, plus ping/pong/close control frames. No third-party
dependency.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import ssl
import struct
from urllib.parse import urlsplit

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

_OP_CONT = 0x0
_OP_TEXT = 0x1
_OP_BINARY = 0x2
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA


class WsError(Exception):
    pass


def _mask(data: bytes, key: bytes) -> bytes:
    return bytes(byte ^ key[i % 4] for i, byte in enumerate(data))


def encode_frame(data: bytes, opcode: int = _OP_BINARY) -> bytes:
    """A client-to-server WebSocket frame (always masked)."""
    header = bytearray([0x80 | opcode])
    length = len(data)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header += struct.pack("!H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack("!Q", length)
    key = os.urandom(4)
    header += key
    return bytes(header) + _mask(data, key)


def _handshake_request(host: str, port: int, path: str, key: str) -> bytes:
    return (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    ).encode("latin1")


def _expected_accept(key: str) -> str:
    digest = hashlib.sha1((key + _GUID).encode("latin1")).digest()
    return base64.b64encode(digest).decode("latin1")


class WsConnection:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer

    @classmethod
    async def connect(cls, url: str, *, timeout: float = 15.0) -> WsConnection:
        parts = urlsplit(url)
        secure = parts.scheme in ("wss", "https")
        host = parts.hostname or "localhost"
        port = parts.port or (443 if secure else 80)
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        context = ssl.create_default_context() if secure else None
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                host, port, ssl=context, server_hostname=host if secure else None
            ),
            timeout=timeout,
        )
        key = base64.b64encode(os.urandom(16)).decode("latin1")
        try:
            writer.write(_handshake_request(host, port, path, key))
            await writer.drain()
            status_line = await reader.readline()
            headers: dict[str, str] = {}
            while True:
                line = await reader.readline()
                if line in (b"", b"\r\n", b"\n"):
                    break
                name, _, value = line.decode("latin1").partition(":")
                headers[name.strip().lower()] = value.strip()
            if b" 101 " not in status_line and not status_line.startswith(b"HTTP/1.1 101"):
                raise WsError(f"upgrade failed: {status_line.decode('latin1').strip()}")
            if headers.get("sec-websocket-accept") != _expected_accept(key):
                raise WsError("invalid Sec-WebSocket-Accept")
        except Exception:
            writer.close()
            raise
        return cls(reader, writer)

    async def send(self, data: bytes) -> None:
        self._writer.write(encode_frame(data, _OP_BINARY))
        await self._writer.drain()

    async def recv(self) -> bytes | None:
        """Return the next binary message, or ``None`` when the peer closes."""
        fragments = bytearray()
        while True:
            first = await self._reader.readexactly(2)
            opcode = first[0] & 0x0F
            masked = bool(first[1] & 0x80)
            length = first[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", await self._reader.readexactly(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", await self._reader.readexactly(8))[0]
            key = await self._reader.readexactly(4) if masked else None
            payload = await self._reader.readexactly(length)
            if key is not None:
                payload = _mask(payload, key)
            if opcode == _OP_CLOSE:
                return None
            if opcode == _OP_PING:
                await self._send_control(_OP_PONG, payload)
                continue
            if opcode == _OP_PONG:
                continue
            if opcode in (_OP_TEXT, _OP_BINARY, _OP_CONT):
                fragments += payload
                if first[0] & 0x80:  # FIN
                    return bytes(fragments)
            # ignore unknown opcodes

    async def _send_control(self, opcode: int, payload: bytes) -> None:
        self._writer.write(encode_frame(payload, opcode))
        await self._writer.drain()

    def abort(self) -> None:
        self._writer.close()

    async def close(self) -> None:
        self._writer.close()


__all__ = ["WsConnection", "WsError", "encode_frame"]
