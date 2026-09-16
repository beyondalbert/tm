"""Approval prompts for actions the policy does not decide automatically."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from rich.console import Console

from tm.permissions.actions import Action


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
    """Ask the user on the terminal. 'always' remembers the decision."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    async def request(self, action: Action, reason: str) -> ApprovalOutcome:
        self.console.print(
            f"[bold yellow]permission[/bold yellow] {action.describe()} [dim]({reason})[/dim]"
        )
        try:
            answer = await asyncio.to_thread(input, "  allow? [y]es/[n]o/[a]lways: ")
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
