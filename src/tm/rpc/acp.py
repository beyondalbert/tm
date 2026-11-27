"""ACP (Agent Client Protocol) adapter: a documented subset for editors.

Supported: ``initialize``, ``authenticate`` (no-op), ``session/new``,
``session/load`` (existing session only), ``session/prompt``, ``session/cancel``,
agent-to-client ``session/update`` notifications, and
``session/request_permission`` requests.

Not supported yet: MCP servers passed by the client, slash commands, and
resource links. See ``docs/design/embedding.md``.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from tm import __version__
from tm.permissions.actions import Action, ActionKind
from tm.permissions.approval import ApprovalOutcome
from tm.rpc.host import ServerHost, Wire
from tm.rpc.protocol import (
    INVALID_PARAMS,
    RpcError,
    notification,
    require_str,
)
from tm.rpc.server import HandlerFn, Request, Send

ACP_PROTOCOL_VERSION = 1

_TOOL_KINDS = {
    ActionKind.FILE_READ: "read",
    ActionKind.FILE_WRITE: "edit",
    ActionKind.SHELL: "execute",
    ActionKind.NETWORK: "fetch",
    ActionKind.ELEVATED: "other",
}

_STOP_REASONS = {
    "aborted": "cancelled",
    "failed": "end_turn",
}


class AcpApprover:
    """Asks the ACP client through ``session/request_permission``."""

    def __init__(
        self,
        wire: Wire,
        *,
        session_id: str | None,
        timeout_ms: int = 120_000,
    ) -> None:
        self._wire = wire
        self._session_id = session_id
        self._timeout_ms = max(1, int(timeout_ms))
        self._counter = 0

    async def request(self, action: Action, reason: str) -> ApprovalOutcome:
        self._counter += 1
        payload = {
            "sessionId": self._session_id,
            "toolCall": {
                "toolCallId": f"perm-{self._counter}",
                "title": action.describe(),
                "kind": _TOOL_KINDS.get(action.kind, "other"),
                "status": "pending",
                "rawInput": {"target": action.target, "reason": reason},
            },
            "options": [
                {"optionId": "allow", "name": "Allow once", "kind": "allow_once"},
                {
                    "optionId": "allow_scope",
                    "name": "Always allow",
                    "kind": "allow_always",
                },
                {"optionId": "deny", "name": "Reject", "kind": "reject_once"},
            ],
        }
        try:
            result = await self._wire.request(
                "session/request_permission",
                payload,
                self._timeout_ms / 1000.0,
            )
        except RpcError:
            return ApprovalOutcome(allowed=False, source="host-timeout")
        option: str | None = None
        if isinstance(result, dict):
            outcome = result.get("outcome")
            if isinstance(outcome, dict) and isinstance(outcome.get("optionId"), str):
                option = outcome["optionId"]
        allowed = option in ("allow", "allow_scope")
        return ApprovalOutcome(
            allowed=allowed,
            remember=allowed and option == "allow_scope",
            source="host",
        )


def _partial_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


class AcpServer:
    """Serves one :class:`ServerHost` over the Agent Client Protocol."""

    def __init__(
        self, host: ServerHost, *, name: str = "tm", version: str = __version__
    ) -> None:
        self._host = host
        self._name = name
        self._version = version
        self._send: Send | None = None
        self._stop: asyncio.Event | None = None
        self._wire = Wire()
        #: turnId -> how many characters of the assistant message were forwarded.
        self._forwarded: dict[str, int] = {}

    async def start(self, send: Send, request: Request) -> dict[str, HandlerFn]:
        self._send = send
        self._stop = asyncio.Event()
        self._wire.bind(self._forward, request)
        await self._host.start(self._wire.send, self._wire.request)
        return {
            "initialize": self.initialize,
            "authenticate": self.authenticate,
            "session/new": self.session_new,
            "session/load": self.session_load,
            "session/prompt": self.session_prompt,
            "session/cancel": self.session_cancel,
        }

    async def close(self) -> None:
        await self._host.close()

    async def wait_stopped(self) -> None:
        await self._host.wait_stopped()

    # -- host -> client translation ---------------------------------------
    async def _forward(self, payload: dict[str, Any]) -> None:
        method = payload.get("method")
        params = payload.get("params") or {}
        if method == "event":
            await self._forward_event(params)
        elif method == "turn/end":
            self._forwarded.pop(str(params.get("turnId")), None)
        # permission/request is unused here: AcpApprover sends its own request.

    async def _forward_event(self, params: dict[str, Any]) -> None:
        if self._send is None:
            return
        event = params.get("event") or {}
        session_id = params.get("sessionId")
        turn_id = str(params.get("turnId") or "")
        kind = event.get("type")
        if kind == "message_update":
            text = _partial_text(event.get("message"))
            forwarded = self._forwarded.get(turn_id, 0)
            if len(text) > forwarded:
                await self._send(
                    notification(
                        "session/update",
                        {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": text[forwarded:]},
                            },
                        },
                    )
                )
                self._forwarded[turn_id] = len(text)
            return
        if kind == "tool_execution_start":
            await self._send(
                notification(
                    "session/update",
                    {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": event.get("tool_call_id"),
                            "title": event.get("tool_name"),
                            "kind": "other",
                            "status": "in_progress",
                            "rawInput": event.get("arguments") or {},
                        },
                    },
                )
            )
            return
        if kind == "tool_execution_end":
            await self._send(
                notification(
                    "session/update",
                    {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": event.get("tool_call_id"),
                            "status": (
                                "failed" if event.get("is_error") else "completed"
                            ),
                            "content": [
                                {
                                    "type": "content",
                                    "content": {
                                        "type": "text",
                                        "text": str(event.get("output") or ""),
                                    },
                                }
                            ],
                        },
                    },
                )
            )

    # -- methods ----------------------------------------------------------
    async def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocolVersion": ACP_PROTOCOL_VERSION,
            "agentCapabilities": {
                "loadSession": True,
                "promptCapabilities": {
                    "image": False,
                    "audio": False,
                    "embeddedContext": False,
                },
            },
            "agentInfo": {"name": self._name, "version": self._version},
        }

    async def authenticate(self, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def session_new(self, params: dict[str, Any]) -> dict[str, Any]:
        raw = params.get("cwd")
        cwd = str(raw) if isinstance(raw, str) and raw else str(Path.cwd())
        info = await self._host.session_create({"cwd": cwd})
        return {"sessionId": info["sessionId"]}

    async def session_load(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = require_str(params, "sessionId")
        await self._host.session_info({"sessionId": session_id})
        return {}

    async def session_prompt(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = require_str(params, "sessionId")
        prompt = params.get("prompt")
        if not isinstance(prompt, list):
            raise RpcError(INVALID_PARAMS, "prompt must be a list of content blocks")
        parts: list[str] = []
        for block in prompt:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        content = "\n".join(parts).strip()
        if not content:
            raise RpcError(INVALID_PARAMS, "prompt must contain a text block")
        end = await self._host.run_to_completion(content, session_id=session_id)
        status = str(end.get("status") or "done")
        if status == "failed" and self._send is not None:
            await self._send(
                notification(
                    "session/update",
                    {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {
                                "type": "text",
                                "text": f"\n[{end.get('error') or 'failed'}]\n",
                            },
                        },
                    },
                )
            )
        if end.get("stopReason") == "max_turns":
            return {"stopReason": "max_turn_requests"}
        return {"stopReason": _STOP_REASONS.get(status, "end_turn")}

    async def session_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = params.get("sessionId")
        request: dict[str, Any] = {}
        if isinstance(session_id, str):
            request["sessionId"] = session_id
        with contextlib.suppress(RpcError):
            await self._host.cancel(request)
        return {}


__all__ = ["AcpApprover", "AcpServer"]
