from __future__ import annotations

import pytest

from tm.rpc.protocol import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    NOTIFICATION,
    PARSE_ERROR,
    REQUEST,
    RESPONSE,
    RpcError,
    error_response,
    notification,
    optional_bool,
    optional_int,
    optional_str,
    optional_str_list,
    parse_message,
    require_str,
    result_response,
    serialize,
)


def test_parse_request() -> None:
    message = parse_message(
        '{"jsonrpc":"2.0","id":7,"method":"prompt","params":{"content":"hi"}}'
    )
    assert message.kind == REQUEST
    assert message.method == "prompt"
    assert message.params == {"content": "hi"}
    assert message.request_id == 7


def test_parse_request_without_params_defaults_to_empty() -> None:
    message = parse_message('{"jsonrpc":"2.0","id":"a","method":"shutdown"}')
    assert message.kind == REQUEST
    assert message.params == {}


def test_parse_notification_has_no_id() -> None:
    message = parse_message('{"method":"event"}')
    assert message.kind == NOTIFICATION
    assert message.method == "event"
    assert message.request_id is None


def test_parse_response_carries_result_or_error() -> None:
    ok = parse_message('{"id":3,"result":{"ready":true}}')
    assert ok.kind == RESPONSE
    assert ok.request_id == 3
    assert ok.result == {"ready": True}

    failed = parse_message('{"id":4,"error":{"code":-32601,"message":"nope"}}')
    assert failed.kind == RESPONSE
    assert failed.error == {"code": -32601, "message": "nope"}


def test_parse_rejects_bad_json() -> None:
    with pytest.raises(RpcError) as caught:
        parse_message("{not json")
    assert caught.value.code == PARSE_ERROR


def test_parse_rejects_non_object() -> None:
    with pytest.raises(RpcError) as caught:
        parse_message("[1,2,3]")
    assert caught.value.code == INVALID_REQUEST


def test_parse_rejects_array_params() -> None:
    with pytest.raises(RpcError) as caught:
        parse_message('{"id":1,"method":"prompt","params":["hi"]}')
    assert caught.value.code == INVALID_PARAMS


def test_parse_rejects_wrong_jsonrpc_version() -> None:
    with pytest.raises(RpcError) as caught:
        parse_message('{"jsonrpc":"1.0","id":1,"method":"initialize"}')
    assert caught.value.code == INVALID_REQUEST


def test_parse_rejects_missing_method() -> None:
    with pytest.raises(RpcError) as caught:
        parse_message('{"id":1,"params":{}}')
    assert caught.value.code == INVALID_REQUEST


def test_result_and_error_envelopes() -> None:
    assert result_response(1, {"ok": True}) == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"ok": True},
    }
    error = error_response(1, INVALID_PARAMS, "bad", {"key": "content"})
    assert error["error"] == {
        "code": INVALID_PARAMS,
        "message": "bad",
        "data": {"key": "content"},
    }
    assert "data" not in error_response(None, PARSE_ERROR, "bad")["error"]


def test_serialize_is_one_line_and_keeps_unicode() -> None:
    line = serialize(notification("event", {"text": "中文\n第二行"}))
    assert "\n" not in line
    assert "中文" in line


def test_param_helpers() -> None:
    assert require_str({"content": "hi"}, "content") == "hi"
    with pytest.raises(RpcError) as caught:
        require_str({"content": "  "}, "content")
    assert caught.value.code == INVALID_PARAMS

    assert optional_str({}, "key") is None
    with pytest.raises(RpcError):
        optional_str({"key": 5}, "key")

    assert optional_bool({}, "remember") is False
    assert optional_bool({"remember": True}, "remember") is True
    with pytest.raises(RpcError):
        optional_bool({"remember": "yes"}, "remember")

    assert optional_int({}, "count") is None
    assert optional_int({"count": 2}, "count") == 2
    with pytest.raises(RpcError):
        optional_int({"count": True}, "count")

    assert optional_str_list({"tools": ["read"]}, "tools") == ["read"]
    assert optional_str_list({}, "tools") is None
    with pytest.raises(RpcError):
        optional_str_list({"tools": [1]}, "tools")
