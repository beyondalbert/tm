"""Approval prompts for actions the policy does not decide automatically."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rich.console import Console

from tm.permissions.actions import Action, ActionKind


@dataclass
class ApprovalOutcome:
    allowed: bool
    remember: bool = False


class Approver(Protocol):
    async def request(self, action: Action, reason: str) -> ApprovalOutcome: ...


class AutoDenyApprover:
    async def request(self, action: Action, reason: str) -> ApprovalOutcome:
        return ApprovalOutcome(allowed=False)


class AutoAllowApprover:
    async def request(self, action: Action, reason: str) -> ApprovalOutcome:
        return ApprovalOutcome(allowed=True)


class SessionApprover:
    """Wraps an approver and can auto-approve for the rest of the session.

    Policy ``deny`` rules are still enforced: they are decided before the
    approver is consulted. This matches ``--yolo`` and can be toggled with
    ``/auto on|off`` (Ctrl+Y in the TUI).
    """

    def __init__(self, inner: Approver, auto: bool = False) -> None:
        self.inner = inner
        self.auto = auto

    async def request(self, action: Action, reason: str) -> ApprovalOutcome:
        if self.auto:
            return ApprovalOutcome(allowed=True)
        return await self.inner.request(action, reason)


class ConsoleApprover:
    """Ask the user on the terminal. 'always' remembers a scope: a folder for
    file actions, a program for shell commands, a host for network actions."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    @staticmethod
    def _scope(action: Action) -> str:
        if action.kind in (ActionKind.FILE_READ, ActionKind.FILE_WRITE):
            path = Path(action.target).expanduser()
            if not path.is_absolute():
                path = Path.cwd() / path
            folder = path if path.is_dir() else path.parent
            return f"this folder ({folder})"
        if action.kind is ActionKind.SHELL:
            tokens = action.target.strip().split()
            program = tokens[0] if tokens else action.target.strip()
            return f"every `{program}` command"
        return "this host"

    async def request(self, action: Action, reason: str) -> ApprovalOutcome:
        self.console.print(
            f"[bold yellow]permission[/bold yellow] {action.describe()} [dim]({reason})[/dim]"
        )
        prompt = f"  allow? [y]es once / [a]lways {self._scope(action)} / [n]o: "
        try:
            answer = await asyncio.to_thread(input, prompt)
        except EOFError:
            return ApprovalOutcome(allowed=False)
        normalized = answer.strip().lower()
        if normalized in ("a", "always"):
            return ApprovalOutcome(allowed=True, remember=True)
        return ApprovalOutcome(allowed=normalized in ("y", "yes"))


__all__ = [
    "ApprovalOutcome",
    "Approver",
    "AutoAllowApprover",
    "AutoDenyApprover",
    "ConsoleApprover",
    "SessionApprover",
]
