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
        # "Always allow" remembers a scope: the containing folder for file
        # actions, the exact command/host otherwise.
        self._remembered_folders: set[Path] = set()
        self._remembered_keys: set[str] = set()

    def _absolute(self, action: Action) -> Path:
        path = Path(action.target).expanduser()
        if not path.is_absolute():
            path = self.policy.cwd / path
        return path.resolve()

    def _is_remembered(self, action: Action) -> bool:
        if action.kind in (ActionKind.FILE_READ, ActionKind.FILE_WRITE):
            path = self._absolute(action)
            return any(path == folder or folder in path.parents for folder in self._remembered_folders)
        return action.key in self._remembered_keys

    def _remember(self, action: Action) -> str:
        if action.kind in (ActionKind.FILE_READ, ActionKind.FILE_WRITE):
            path = self._absolute(action)
            folder = path if path.is_dir() else path.parent
            self._remembered_folders.add(folder)
            return str(folder)
        self._remembered_keys.add(action.key)
        return action.key

    async def authorize(self, action: Action) -> bool:
        decision, reason = self.policy.evaluate(action)
        if decision is Decision.ASK and self._is_remembered(action):
            decision, reason = Decision.ALLOW, "remembered decision"

        if decision is Decision.ALLOW:
            self.audit.record(action=action, decision=decision, allowed=True, reason=reason)
            return True
        if decision is Decision.DENY:
            self.audit.record(action=action, decision=decision, allowed=False, reason=reason)
            return False

        outcome = await self.approver.request(action, reason)
        if outcome.remember and outcome.allowed:
            scope = self._remember(action)
            reason = f"user decision (remembered {scope})"
        else:
            reason = "user decision"
        self.audit.record(
            action=action,
            decision=Decision.ALLOW if outcome.allowed else Decision.DENY,
            allowed=outcome.allowed,
            reason=reason,
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
