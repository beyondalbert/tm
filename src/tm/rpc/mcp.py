"""Minimal MCP server: expose TM as a ``tm_run`` tool to any MCP host.

MCP hosts (Claude Desktop, MCP-aware editors, other agents) get one tool that
runs a coding task with TM's tools and policy. Permissions follow the process
``--mode``; with the default ``policy`` every ``ask`` action is denied, so an MCP
call can never approve itself.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from tm import __version__
from tm.rpc.host import ServerHost, Wire
from tm.rpc.protocol import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    RpcError,
    optional_str,
    require_str,
)
from tm.rpc.server import HandlerFn, Request, Send

MCP_PROTOCOL_VERSION = "2025-06-18"

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "tm_run",
        "description": (
            "Run a coding task on this machine with TM: read, edit, search, and "
            "run commands under TM's permission policy. Returns the final answer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The task or question."},
                "cwd": {
                    "type": "string",
                    "description": "Working directory (default: the server's cwd).",
                },
                "idempotencyKey": {
                    "type": "string",
                    "description": "Reuse the earlier result of this key instead of re-running.",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "tm_session_info",
        "description": "Report the open session's id, cwd, model, and message count.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _summary_line(change: dict[str, Any]) -> str:
    path = change.get("path")
    if change.get("kind") != "file":
        return f"- {path}: {change.get('summary') or change.get('kind')}"
    added = change.get("added")
    removed = change.get("removed")
    if added is None or removed is None:
        return f"- {path}"
    return f"- {path} (+{added}/-{removed})"


class McpServer:
    def __init__(
        self, host: ServerHost, *, name: str = "tm", version: str = __version__
    ) -> None:
        self._host = host
        self._name = name
        self._version = version
        self._stop: asyncio.Event | None = None
        self._by_cwd: dict[str, str] = {}

    async def start(self, send: Send, request: Request) -> dict[str, HandlerFn]:
        self._stop = asyncio.Event()
        wire = Wire()
        wire.bind(send, request)
        await self._host.start(wire.send, wire.request)
        return {
            "initialize": self.initialize,
            "ping": self.ping,
            "tools/list": self.tools_list,
            "tools/call": self.tools_call,
        }

    async def close(self) -> None:
        await self._host.close()

    async def wait_stopped(self) -> None:
        await self._host.wait_stopped()

    # -- methods ----------------------------------------------------------
    async def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self._name, "version": self._version},
        }

    async def ping(self, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"tools": _TOOLS}

    async def tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = require_str(params, "name")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise RpcError(INVALID_PARAMS, "arguments must be an object")
        if name == "tm_session_info":
            return self._text(await self._info_text())
        if name != "tm_run":
            raise RpcError(METHOD_NOT_FOUND, f"unknown tool: {name}")
        prompt = require_str(arguments, "prompt")
        cwd = optional_str(arguments, "cwd")
        key = optional_str(arguments, "idempotencyKey")
        session_id: str | None = None
        if cwd is not None:
            session_id = await self._session_for(cwd)
        end = await self._host.run_to_completion(
            prompt, session_id=session_id, idempotency_key=key
        )
        text = str(end.get("text") or "").strip()
        changed = end.get("changedFiles") or []
        if changed:
            lines = "\n".join(_summary_line(change) for change in changed)
            text = f"{text}\n\nChanged files:\n{lines}".strip()
        status = end.get("status")
        if status != "done":
            detail = end.get("error") or status
            text = f"{text}\n\n[tm: turn ended as {status}: {detail}]".strip()
        return {"content": [{"type": "text", "text": text or "(no output)"}], "isError": status != "done"}

    # -- helpers ----------------------------------------------------------
    async def _session_for(self, cwd: str) -> str:
        path = Path(cwd).expanduser().resolve()
        if not path.is_dir():
            raise RpcError(INVALID_PARAMS, f"cwd is not a directory: {path}")
        existing = self._by_cwd.get(str(path))
        if existing is not None:
            with contextlib.suppress(RpcError):
                await self._host.session_info({"sessionId": existing})
                return existing
            self._by_cwd.pop(str(path), None)
        info = await self._host.session_create({"cwd": str(path)})
        self._by_cwd[str(path)] = info["sessionId"]
        return info["sessionId"]

    async def _info_text(self) -> str:
        info = await self._host.session_info({})
        model = info.get("model") or {}
        return (
            f"session {info.get('sessionId')}\n"
            f"cwd {info.get('cwd')}\n"
            f"access {info.get('access')}\n"
            f"model {model.get('provider')}/{model.get('id')}\n"
            f"messages {info.get('messageCount')}"
        )

    @staticmethod
    def _text(text: str) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": text}], "isError": False}


__all__ = ["MCP_PROTOCOL_VERSION", "McpServer"]
