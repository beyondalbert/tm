"""Actions subject to permission checks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ActionKind(StrEnum):
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    SHELL = "shell"
    NETWORK = "network"


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    target: str
    tool_name: str = ""

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.target}"

    def describe(self) -> str:
        return f"{self.kind.value}: {self.target}"


__all__ = ["Action", "ActionKind", "Decision"]
