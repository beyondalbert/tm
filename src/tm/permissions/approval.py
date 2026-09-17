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


class ConsoleApprover:
    """Ask the user on the terminal. 'always' remembers a scope (a folder for
    file actions, the exact command/host otherwise)."""

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
            return "this command"
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
]
