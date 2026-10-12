from __future__ import annotations

import json

import pytest

from tm.im import protocol as p


def test_varint_matches_documented_table() -> None:
    table = {
        1: "01",
        2: "02",
        10: "0a",
        127: "7f",
        128: "8001",
        300: "ac02",
        1001: "e907",
        16384: "808001",
    }
    for value, expected in table.items():
        encoded = p.encode_varint(value)
        assert encoded.hex() == expected
        decoded, index = p.decode_varint(encoded, 0)
        assert decoded == value
        assert index == len(encoded)


def test_auth_frame_matches_documented_bytes() -> None:
    frame = p.encode_frame(
        p.Cmd.AUTH,
        1,
        p.encode_auth_request("sk_live_9f2c1d7a4b8e3f60", "1.0.0", "web-chrome-131"),
    )
    expected = (
        "080110011a310a18736b5f6c6976655f3966326331643761346238653366363012053"
        "12e302e301a0e7765622d6368726f6d652d313331"
    )
    assert frame.hex() == expected
    assert len(frame) == 55


def test_send_frame_matches_documented_bytes() -> None:
    content = json.dumps({"text": "你好，Agent"}, ensure_ascii=False, separators=(",", ":"))
    frame = p.encode_frame(
        p.Cmd.SEND, 2, p.encode_send_request(1001, "c-7f3a9b21", 1, content)
    )
    expected = (
        "080a10021a2c08e907120a632d3766336139623231180122197b2274657874223a22e4b"
        "da0e5a5bdefbc8c4167656e74227d"
    )
    assert frame.hex() == expected
    assert len(frame) == 50

    decoded = p.decode_frame(frame)
    assert decoded.cmd == p.Cmd.SEND
    fields = p.decode_fields(decoded.payload)
    assert p._as_int(fields, 1) == 1001
    assert p._as_str(fields, 2) == "c-7f3a9b21"


def test_send_ack_matches_documented_bytes() -> None:
    raw = bytes.fromhex(
        "080b10021a2208e9071007188180a4f09de4de900a20fbd0eab6b7332a0a632d3766336139623231"
    )
    frame = p.decode_frame(raw)
    assert frame.cmd == p.Cmd.SEND_ACK
    assert frame.req_id == 2
    ack = p.decode_send_ack(frame.payload)
    assert ack.conv_id == 1001
    assert ack.seq == 7
    assert ack.message_id == 730000000000000001
    assert ack.client_msg_id == "c-7f3a9b21"


def test_push_frame_matches_documented_bytes() -> None:
    raw = bytes.fromhex(
        "080c1a420a40088280a4f09de4de900a10e907180820d20f280132237b2274657874223a22"
        "e694b6e588b0efbc8ce4bb8ae5a4a9e58c97e4baace699b4227d40c8d3eab6b733"
    )
    frame = p.decode_frame(raw)
    assert frame.cmd == p.Cmd.PUSH
    assert frame.req_id == 0  # server push omits req_id (proto3 default)
    message = p.decode_push(frame.payload)
    assert message.conv_id == 1001
    assert message.seq == 8
    assert message.sender_id == 2002
    assert message.msg_type == p.MsgType.TEXT
    assert message.text() == "收到，今天北京晴"


def test_sync_response_matches_documented_bytes() -> None:
    raw = bytes.fromhex(
        "081010031a750a40088280a4f09de4de900a10e907180820d20f280132237b227465787422"
        "3a22e694b6e588b0efbc8ce4bb8ae5a4a9e58c97e4baace699b4227d40c8d3eab6b7330a3108"
        "8380a4f09de4de900a10e907180920e907280132147b2274657874223a22e6988ee5a4a9e8a7"
        "81227d4095d6eab6b733"
    )
    frame = p.decode_frame(raw)
    assert frame.cmd == p.Cmd.SYNC_RESP
    response = p.decode_sync_response(frame.payload)
    assert [m.seq for m in response.messages] == [8, 9]
    assert response.messages[1].text() == "明天见"
    assert response.has_more is False
    assert response.truncated is False


def test_sync_end_matches_documented_bytes() -> None:
    raw = bytes.fromhex("080f10031a0408011802")
    frame = p.decode_frame(raw)
    assert frame.cmd == p.Cmd.SYNC_END
    end = p.decode_sync_end(frame.payload)
    assert end.ok is True
    assert end.conv_synced == 2


def test_sync_request_encodes_cursors_and_limit() -> None:
    payload = p.encode_sync_request({1001: 7, 1002: 23}, limit=200)
    fields = p.decode_fields(payload)
    cursors = p._repeated_bytes(payload, 1)
    assert len(cursors) == 2
    first = p.decode_fields(cursors[0])
    assert p._as_int(first, 1) == 1001
    assert p._as_int(first, 2) == 7
    assert p._as_int(fields, 2) == 200


def test_default_values_are_not_encoded() -> None:
    frame = p.encode_frame(p.Cmd.PING, 0)
    assert frame == bytes([0x08, 0x03])  # only cmd; req_id=0 and empty payload omitted


def test_encode_send_request_roundtrips() -> None:
    payload = p.encode_send_request(5, "c-1", p.MsgType.TEXT, "{}")
    fields = p.decode_fields(payload)
    assert p._as_int(fields, 1) == 5
    assert p._as_int(fields, 3) == 1


def test_decode_varint_truncated_raises() -> None:
    with pytest.raises(ValueError):
        p.decode_varint(b"\x80", 0)
