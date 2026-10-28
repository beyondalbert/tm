from __future__ import annotations

import struct

from tm.im.config import ImSettings
from tm.im.websocket import _expected_accept, _mask, encode_frame


def _decode_client_frame(frame: bytes) -> tuple[int, bytes]:
    """Parse a (masked) client frame for assertions."""
    byte0, byte1 = frame[0], frame[1]
    opcode = byte0 & 0x0F
    assert byte0 & 0x80  # FIN
    assert byte1 & 0x80  # masked
    length = byte1 & 0x7F
    offset = 2
    if length == 126:
        length = struct.unpack("!H", frame[offset : offset + 2])[0]
        offset += 2
    elif length == 127:
        length = struct.unpack("!Q", frame[offset : offset + 8])[0]
        offset += 8
    key = frame[offset : offset + 4]
    offset += 4
    payload = _mask(frame[offset : offset + length], key)
    return opcode, payload


def test_encode_frame_binary_small() -> None:
    opcode, payload = _decode_client_frame(encode_frame(b"hello"))
    assert opcode == 0x2
    assert payload == b"hello"


def test_encode_frame_medium_uses_16bit_length() -> None:
    data = b"x" * 300
    opcode, payload = _decode_client_frame(encode_frame(data))
    assert opcode == 0x2
    assert payload == data


def test_encode_frame_large_uses_64bit_length() -> None:
    data = b"y" * 70000
    _, payload = _decode_client_frame(encode_frame(data))
    assert payload == data


def test_rfc_accept_vector() -> None:
    assert _expected_accept("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_websocket_url_normalization() -> None:
    http = ImSettings(base_url="http://tmim.stkg.ltd/v1", ws_url="tmim.stkg.ltd/ws")
    assert http.websocket_url() == "ws://tmim.stkg.ltd/ws"
    https = ImSettings(base_url="https://tmim.stkg.ltd/v1", ws_url="tmim.stkg.ltd/ws")
    assert https.websocket_url() == "wss://tmim.stkg.ltd/ws"
    explicit = ImSettings(ws_url="wss://host:9000/custom")
    assert explicit.websocket_url() == "wss://host:9000/custom"
    # A full REST-style URL is converted to ws/wss (not left as http://...).
    assert ImSettings(ws_url="http://tmim.stkg.ltd/ws").websocket_url() == "ws://tmim.stkg.ltd/ws"
    assert ImSettings(ws_url="https://tmim.stkg.ltd/ws").websocket_url() == "wss://tmim.stkg.ltd/ws"
    # A doubled scheme (e.g. from an earlier bug) is recovered.
    assert ImSettings(ws_url="ws://http://tmim.stkg.ltd/ws").websocket_url() == "ws://tmim.stkg.ltd/ws"


def test_websocket_url_defaults_to_host_ws() -> None:
    assert ImSettings().websocket_url() == "ws://tmim.stkg.ltd/ws"
    https = ImSettings(base_url="https://example.com/v1")
    assert https.websocket_url() == "wss://example.com/ws"
    # An explicit tcp_addr opts into native TCP when ws_url is unset.
    assert ImSettings(tcp_addr="example.com:9000").websocket_url() is None
    # An explicit ws_url still wins over tcp_addr.
    both = ImSettings(tcp_addr="example.com:9000", ws_url="example.com/ws")
    assert both.websocket_url() == "ws://example.com/ws"
