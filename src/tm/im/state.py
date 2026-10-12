"""Durable bot state: per-conversation cursors and session mapping.

Only two things must survive a restart to resume cleanly: the ``seq`` we have
processed in each conversation, and which TM session serves each conversation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class ImState:
    def __init__(
        self,
        path: Path,
        cursors: dict[int, int] | None = None,
        sessions: dict[int, str] | None = None,
    ) -> None:
        self.path = path
        self.cursors: dict[int, int] = dict(cursors or {})
        self.sessions: dict[int, str] = dict(sessions or {})

    @classmethod
    def load(cls, path: Path) -> ImState:
        if not path.exists():
            return cls(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls(path)
        cursors = {
            int(conv): int(seq)
            for conv, seq in dict(data.get("cursors", {})).items()
            if str(conv).lstrip("-").isdigit()
        }
        sessions = {
            int(conv): str(value) for conv, value in dict(data.get("sessions", {})).items()
        }
        return cls(path, cursors, sessions)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cursors": {str(conv): seq for conv, seq in self.cursors.items()},
            "sessions": {str(conv): value for conv, value in self.sessions.items()},
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.path)

    # -- cursors ----------------------------------------------------------
    def last_seq(self, conv_id: int) -> int:
        return self.cursors.get(conv_id, 0)

    def set_last_seq(self, conv_id: int, seq: int) -> None:
        if seq > self.cursors.get(conv_id, 0):
            self.cursors[conv_id] = seq

    def set_cursors(self, cursors: dict[int, int]) -> None:
        for conv_id, seq in cursors.items():
            self.set_last_seq(conv_id, seq)

    # -- sessions ---------------------------------------------------------
    def session_path(self, conv_id: int) -> Path | None:
        value = self.sessions.get(conv_id)
        return Path(value) if value else None

    def set_session_path(self, conv_id: int, path: Path) -> None:
        self.sessions[conv_id] = str(path)


__all__ = ["ImState"]
