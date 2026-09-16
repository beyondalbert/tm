"""Append-only audit log of permission decisions and executed actions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tm.permissions.actions import Action, Decision


class AuditLog:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path

    def record(
        self,
        *,
        action: Action,
        decision: Decision,
        allowed: bool,
        reason: str,
    ) -> None:
        if self.path is None:
            return
        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "kind": action.kind.value,
            "target": action.target,
            "tool": action.tool_name,
            "decision": decision.value,
            "allowed": allowed,
            "reason": reason,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass


__all__ = ["AuditLog"]
