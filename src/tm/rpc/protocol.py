"""Wire protocol for ``tm serve --stdio``.

JSON-RPC 2.0 over newline-delimited JSON: one JSON object per line on stdin and
stdout. See ``docs/design/embedding.md`` for the full contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: Bumped on a wire change. ``MAJOR`` breaks hosts, ``MINOR`` only adds.
PROTOCOL_VERSION = "1.0"

# JSON-RPC 2.0 standard codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# TM codes (implementation-defined range -32000..-32099).
BUSY = -32001
NO_TURN = -32002
APPROVAL_TIMEOUT = -32003
UNSUPPORTED = -32004
NO_SESSION = -32005
SESSION_LIMIT = -32006
UNKNOWN_SESSION = -32007
NOT_CONFIGURED = -32008

#: Message kinds returned by :func:`parse_message`.
REQUEST = "request"
NOTIFICATION = "notification"
RESPONSE = "response"


class RpcError(Exception):
    """An error reported to the host as a JSON-RPC error object."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


@dataclass
class ParsedMessage:
    kind: str
    method: str = ""
    params: dict = field(default_factory=dict)
    request_id: Any = None
    result: Any = None
    error: dict | None = None


def result_response(request_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(
    request_id: Any, code: int, message: str, data: Any = None
) -> dict:
    error: dict = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def notification(method: str, params: Any) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def serialize(payload: dict) -> str:
    """One wire line: compact JSON, no trailing newline."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def parse_message(line: str) -> ParsedMessage:
    """Parse one line. Raises :class:`RpcError` for malformed input."""
    try:
        data = json.loads(line)
    except ValueError as exc:
        raise RpcError(PARSE_ERROR, f"parse error: {exc}") from exc
    if not isinstance(data, dict):
        raise RpcError(INVALID_REQUEST, "message must be a JSON object")

    version = data.get("jsonrpc")
    if version is not None and version != "2.0":
        raise RpcError(INVALID_REQUEST, 'jsonrpc must be "2.0"')

    request_id = data.get("id")
    if request_id is not None and not isinstance(request_id, (str, int)):
        raise RpcError(INVALID_REQUEST, "id must be a string, number, or null")

    method = data.get("method")
    if method is None:
        if "result" in data or "error" in data:
            error = data.get("error")
            return ParsedMessage(
                kind=RESPONSE,
                request_id=request_id,
                result=data.get("result"),
                error=error if isinstance(error, dict) else None,
            )
        raise RpcError(INVALID_REQUEST, "message has no method")
    if not isinstance(method, str) or not method:
        raise RpcError(INVALID_REQUEST, "method must be a non-empty string")

    params = data.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise RpcError(INVALID_PARAMS, "params must be a JSON object")

    kind = NOTIFICATION if "id" not in data else REQUEST
    return ParsedMessage(
        kind=kind, method=method, params=params, request_id=request_id
    )


def require_str(params: dict, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RpcError(INVALID_PARAMS, f"{key} must be a non-empty string")
    return value


def optional_str(params: dict, key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RpcError(INVALID_PARAMS, f"{key} must be a string")
    return value


def optional_bool(params: dict, key: str, default: bool = False) -> bool:
    value = params.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise RpcError(INVALID_PARAMS, f"{key} must be a boolean")
    return value


def optional_int(params: dict, key: str) -> int | None:
    value = params.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise RpcError(INVALID_PARAMS, f"{key} must be an integer")
    return value


def optional_str_list(params: dict, key: str) -> list[str] | None:
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RpcError(INVALID_PARAMS, f"{key} must be a list of strings")
    return list(value)


def require_dict(params: dict, key: str) -> dict:
    value = params.get(key)
    if not isinstance(value, dict):
        raise RpcError(INVALID_PARAMS, f"{key} must be an object")
    return value


__all__ = [
    "APPROVAL_TIMEOUT",
    "BUSY",
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "NOTIFICATION",
    "NOT_CONFIGURED",
    "NO_SESSION",
    "NO_TURN",
    "PARSE_ERROR",
    "PROTOCOL_VERSION",
    "REQUEST",
    "RESPONSE",
    "RpcError",
    "SESSION_LIMIT",
    "UNKNOWN_SESSION",
    "UNSUPPORTED",
    "ParsedMessage",
    "error_response",
    "notification",
    "optional_bool",
    "optional_int",
    "optional_str",
    "optional_str_list",
    "parse_message",
    "require_dict",
    "require_str",
    "result_response",
    "serialize",
]
