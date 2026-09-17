"""Permission checking and agent integration."""

from __future__ import annotations

from pathlib import Path

from tm.core.agent import BeforeToolCallResult
from tm.permissions.actions import Action, ActionKind, Decision
from tm.permissions.approval import Approver, AutoDenyApprover
from tm.permissions.audit import AuditLog
from tm.permissions.memory import ApprovalMemory
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
        memory: ApprovalMemory | None = None,
    ) -> None:
        self.policy = policy
        self.approver: Approver = approver or AutoDenyApprover()
        self.audit = audit or AuditLog(None)
        self.memory = memory or ApprovalMemory()

    def _absolute(self, action: Action) -> Path:
        path = Path(action.target).expanduser()
        if not path.is_absolute():
            path = self.policy.cwd / path
        return path.resolve()

    @staticmethod
    def _program(command: str) -> str:
        tokens = command.strip().split()
        return (tokens[0] if tokens else command.strip()).lower()

    def _may_remember(self, action: Action) -> bool:
        dangerous = action.kind is ActionKind.SHELL and is_dangerous(action.target)
        return action.kind is not ActionKind.ELEVATED and not dangerous

    def _is_remembered(self, action: Action) -> bool:
        if action.kind in (ActionKind.FILE_READ, ActionKind.FILE_WRITE):
            path = self._absolute(action)
            for folder in self.memory.folders:
                base = Path(folder)
                if path == base or base in path.parents:
                    return True
            return False
        if action.kind is ActionKind.SHELL:
            return self._program(action.target) in self.memory.programs
        if action.kind is ActionKind.NETWORK:
            return action.target.lower() in self.memory.hosts
        return False

    def _remember(self, action: Action) -> str:
        if action.kind in (ActionKind.FILE_READ, ActionKind.FILE_WRITE):
            path = self._absolute(action)
            folder = path if path.is_dir() else path.parent
            self.memory.folders.add(str(folder))
            self.memory.save()
            return f"folder {folder}"
        if action.kind is ActionKind.SHELL:
            program = self._program(action.target)
            self.memory.programs.add(program)
            self.memory.save()
            return f"program `{program}`"
        if action.kind is ActionKind.NETWORK:
            host = action.target.lower()
            self.memory.hosts.add(host)
            self.memory.save()
            return f"host {host}"
        return action.key

    def _evaluate(self, action: Action) -> tuple[Decision, str]:
        """Policy + remembered + dangerous-command decision, before asking."""
        if action.kind is ActionKind.ELEVATED:
            return Decision.ASK, "elevation (never remembered)"
        decision, reason = self.policy.evaluate(action)
        if (
            decision is not Decision.DENY
            and action.kind is ActionKind.SHELL
            and is_dangerous(action.target)
        ):
            return Decision.ASK, "dangerous command requires explicit approval"
        if decision is Decision.ASK and self._is_remembered(action):
            return Decision.ALLOW, "remembered decision"
        return decision, reason

    def _record(self, action: Action, allowed: bool, reason: str) -> None:
        self.audit.record(
            action=action,
            decision=Decision.ALLOW if allowed else Decision.DENY,
            allowed=allowed,
            reason=reason,
        )

    async def _ask(self, actions: list[Action], reason: str) -> bool:
        outcome = await self.approver.request(actions[0], reason)
        if outcome.remember and outcome.allowed:
            remembered = [self._remember(a) for a in actions if self._may_remember(a)]
            reason = (
                f"user decision (remembered {', '.join(remembered)})"
                if remembered
                else "user decision"
            )
        else:
            reason = "user decision"
        for action in actions:
            self._record(action, outcome.allowed, reason)
        return outcome.allowed

    async def authorize(self, action: Action) -> bool:
        return await self.authorize_all([action])

    async def authorize_all(self, actions: list[Action]) -> bool:
        """Authorize a tool call's actions, asking at most once.

        A single denial blocks the whole call; already-allowed actions are
        recorded and never asked. Remaining actions are approved together so
        e.g. a shell+network pair prompts once.
        """
        pending: list[tuple[Action, str]] = []
        for action in actions:
            decision, reason = self._evaluate(action)
            if decision is Decision.DENY:
                self._record(action, False, reason)
                return False
            if decision is Decision.ALLOW:
                self._record(action, True, reason)
                continue
            pending.append((action, reason))
        if not pending:
            return True
        primary_reason = pending[0][1]
        if len(pending) > 1:
            extras = ", ".join(action.describe() for action, _ in pending[1:])
            primary_reason = f"{primary_reason}; also requires: {extras}"
        return await self._ask([action for action, _ in pending], primary_reason)


def build_permission_hook(checker: PermissionChecker, cwd: Path):
    async def hook(call, args) -> BeforeToolCallResult | None:
        actions = actions_for_tool(call.name, call.arguments, cwd)
        if actions and not await checker.authorize_all(actions):
            described = ", ".join(action.describe() for action in actions)
            return BeforeToolCallResult(
                block=True, reason=f"Permission denied: {described}"
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
