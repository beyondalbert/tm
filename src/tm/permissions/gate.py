"""Permission checking and agent integration."""

from __future__ import annotations

from pathlib import Path

from tm.core.agent import BeforeToolCallResult
from tm.permissions.actions import Action, ActionKind, Decision
from tm.permissions.approval import Approver, AutoDenyApprover
from tm.permissions.audit import AuditLog
from tm.permissions.policy import Policy
from tm.safety import is_dangerous

READ_TOOLS = {"read", "grep", "find", "ls"}
WRITE_TOOLS = {"write", "edit"}


def actions_for_tool(tool_name: str, arguments: dict, cwd: Path) -> list[Action]:
    """The permission actions a tool call must pass, in order.

    ``python`` reuses the shell rules (target ``python <description>``) and also
    requires network access when it may install packages. Process, service, and
    package control reuse the shell rules. ``elevate`` is always a separate,
    never-remembered action.
    """
    if tool_name in READ_TOOLS:
        return [Action(ActionKind.FILE_READ, str(arguments.get("path", ".")), tool_name)]
    if tool_name in WRITE_TOOLS:
        return [Action(ActionKind.FILE_WRITE, str(arguments.get("path", "")), tool_name)]
    if tool_name == "shell":
        return [Action(ActionKind.SHELL, str(arguments.get("command", "")), tool_name)]
    if tool_name == "python":
        description = str(arguments.get("description") or "script")
        actions = [Action(ActionKind.SHELL, f"python {description}", tool_name)]
        if arguments.get("packages"):
            actions.append(Action(ActionKind.NETWORK, "pypi.org", tool_name))
        return actions
    if tool_name == "process":
        detail = arguments.get("command") or arguments.get("action") or "list"
        return [Action(ActionKind.SHELL, f"process {detail}", tool_name)]
    if tool_name == "service":
        action = str(arguments.get("action", "status"))
        name = str(arguments.get("name", ""))
        return [Action(ActionKind.SHELL, f"service {action} {name}".strip(), tool_name)]
    if tool_name == "package":
        action = str(arguments.get("action", "query"))
        name = str(arguments.get("name", ""))
        return [Action(ActionKind.SHELL, f"package {action} {name}".strip(), tool_name)]
    if tool_name == "elevate":
        return [Action(ActionKind.ELEVATED, str(arguments.get("command", "")), tool_name)]
    return []


def action_for_tool(tool_name: str, arguments: dict, cwd: Path) -> Action | None:
    actions = actions_for_tool(tool_name, arguments, cwd)
    return actions[0] if actions else None


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
        if action.kind is ActionKind.ELEVATED:
            # Elevation is a trust upgrade: always ask, never remember.
            outcome = await self.approver.request(action, "elevation (never remembered)")
            self.audit.record(
                action=action,
                decision=Decision.ALLOW if outcome.allowed else Decision.DENY,
                allowed=outcome.allowed,
                reason="elevation",
            )
            return outcome.allowed

        decision, reason = self.policy.evaluate(action)
        dangerous = action.kind is ActionKind.SHELL and is_dangerous(action.target)
        if dangerous and decision is not Decision.DENY:
            decision, reason = Decision.ASK, "dangerous command requires explicit approval"
        if decision is Decision.ASK and not dangerous and self._is_remembered(action):
            decision, reason = Decision.ALLOW, "remembered decision"

        if decision is Decision.ALLOW:
            self.audit.record(action=action, decision=decision, allowed=True, reason=reason)
            return True
        if decision is Decision.DENY:
            self.audit.record(action=action, decision=decision, allowed=False, reason=reason)
            return False

        outcome = await self.approver.request(action, reason)
        if outcome.remember and outcome.allowed and not dangerous:
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
        for action in actions_for_tool(call.name, call.arguments, cwd):
            if not await checker.authorize(action):
                return BeforeToolCallResult(
                    block=True, reason=f"Permission denied: {action.describe()}"
                )
        return None

    return hook


__all__ = [
    "PermissionChecker",
    "READ_TOOLS",
    "WRITE_TOOLS",
    "action_for_tool",
    "actions_for_tool",
    "build_permission_hook",
]
