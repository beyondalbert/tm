"""Permission checking and agent integration."""

from __future__ import annotations

from pathlib import Path

from tm.core.agent import BeforeToolCallResult
from tm.permissions.actions import Action, ActionKind, Decision
from tm.permissions.approval import Approver, AutoDenyApprover
from tm.permissions.audit import AuditLog
from tm.permissions.policy import Policy

READ_TOOLS = {"read", "grep", "find", "ls"}
WRITE_TOOLS = {"write", "edit"}


def action_for_tool(tool_name: str, arguments: dict, cwd: Path) -> Action | None:
    if tool_name in READ_TOOLS:
        return Action(ActionKind.FILE_READ, str(arguments.get("path", ".")), tool_name)
    if tool_name in WRITE_TOOLS:
        return Action(ActionKind.FILE_WRITE, str(arguments.get("path", "")), tool_name)
    if tool_name == "shell":
        return Action(ActionKind.SHELL, str(arguments.get("command", "")), tool_name)
    return None


class PermissionChecker:
    def __init__(
        self,
        policy: Policy,
        approver: Approver | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self.policy = policy
        self.approver: Approver = approver or AutoDenyApprover()
        self.audit = audit or AuditLog(None)
        self._remembered: set[str] = set()

    async def authorize(self, action: Action) -> bool:
        decision, reason = self.policy.evaluate(action)
        if decision is Decision.ASK and action.key in self._remembered:
            decision, reason = Decision.ALLOW, "remembered decision"

        if decision is Decision.ALLOW:
            self.audit.record(action=action, decision=decision, allowed=True, reason=reason)
            return True
        if decision is Decision.DENY:
            self.audit.record(action=action, decision=decision, allowed=False, reason=reason)
            return False

        outcome = await self.approver.request(action, reason)
        if outcome.remember and outcome.allowed:
            self._remembered.add(action.key)
        self.audit.record(
            action=action,
            decision=Decision.ALLOW if outcome.allowed else Decision.DENY,
            allowed=outcome.allowed,
            reason="user decision",
        )
        return outcome.allowed


def build_permission_hook(checker: PermissionChecker, cwd: Path):
    async def hook(call, args) -> BeforeToolCallResult | None:
        action = action_for_tool(call.name, call.arguments, cwd)
        if action is None:
            return None
        allowed = await checker.authorize(action)
        if allowed:
            return None
        return BeforeToolCallResult(block=True, reason=f"Permission denied: {action.describe()}")

    return hook


__all__ = [
    "PermissionChecker",
    "READ_TOOLS",
    "WRITE_TOOLS",
    "action_for_tool",
    "build_permission_hook",
]
