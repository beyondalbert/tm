"""Approval bridge for embedded runs: ask the host instead of a terminal.

The host's answer is only ever consulted for actions the policy leaves as
``ask``; deny rules are decided before this point (``PermissionChecker``).
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict

from tm.permissions.actions import Action
from tm.permissions.approval import ApprovalOutcome
from tm.rpc.protocol import notification

Send = Callable[[Dict[str, Any]], Awaitable[None]]

DECISIONS = ("allow", "allow_scope", "deny")


class RpcApprover:
    """Asks the embedding host over the RPC channel and waits for ``approve``."""

    def __init__(
        self, send: Send, *, timeout_ms: int = 120_000, session_id: str | None = None
    ) -> None:
        self._send = send
        self._timeout_ms = max(1, int(timeout_ms))
        self._session_id = session_id
        self._pending: dict[str, asyncio.Future[ApprovalOutcome]] = {}
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"p-{self._counter}"

    async def request(self, action: Action, reason: str) -> ApprovalOutcome:
        loop = asyncio.get_running_loop()
        request_id = self._next_id()
        future: asyncio.Future[ApprovalOutcome] = loop.create_future()
        self._pending[request_id] = future
        payload = {
            "sessionId": self._session_id,
            "requestId": request_id,
            "action": {
                "kind": action.kind.value,
                "target": action.target,
                "tool": action.tool_name,
            },
            "reason": reason,
            "options": list(DECISIONS),
            "deadlineMs": self._timeout_ms,
        }
        try:
            await self._send(notification("permission/request", payload))
            try:
                return await asyncio.wait_for(future, self._timeout_ms / 1000.0)
            except asyncio.TimeoutError:
                await self._send(
                    notification(
                        "permission/expired",
                        {
                            "sessionId": self._session_id,
                            "requestId": request_id,
                            "reason": "timeout",
                        },
                    )
                )
                return ApprovalOutcome(allowed=False, source="host-timeout")
        finally:
            self._pending.pop(request_id, None)

    def resolve(self, request_id: str, decision: str, remember: bool = False) -> bool:
        """Answer a pending request. Returns False when the id is unknown or done."""
        future = self._pending.get(request_id)
        if future is None or future.done():
            return False
        allowed = decision in ("allow", "allow_scope")
        if decision not in DECISIONS:
            return False
        future.set_result(
            ApprovalOutcome(
                allowed=allowed,
                # ``allow_scope`` persists the scope, like answering ``a`` in the TUI.
                remember=allowed and (remember or decision == "allow_scope"),
                source="host",
            )
        )
        return True

    def pending(self) -> list[str]:
        return [key for key, future in self._pending.items() if not future.done()]


__all__ = ["DECISIONS", "RpcApprover", "Send"]
