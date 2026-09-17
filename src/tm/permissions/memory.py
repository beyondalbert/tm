"""Persistent "always allow" decisions, remembered across sessions.

Scopes: a folder for file actions, a program name for shell commands, and a host
for network actions. Stored in ``<config>/approvals.json``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ApprovalMemory:
    path: Path | None = None
    folders: set[str] = field(default_factory=set)
    programs: set[str] = field(default_factory=set)
    hosts: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: Path | None) -> ApprovalMemory:
        memory = cls(path=path)
        if path is None or not path.is_file():
            return memory
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return memory
        if not isinstance(data, dict):
            return memory
        memory.folders = _strings(data.get("folders"))
        memory.programs = _strings(data.get("programs"))
        memory.hosts = _strings(data.get("hosts"))
        return memory

    def save(self) -> None:
        if self.path is None:
            return
        payload = {
            "folders": sorted(self.folders),
            "programs": sorted(self.programs),
            "hosts": sorted(self.hosts),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _strings(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value if isinstance(item, str)}


__all__ = ["ApprovalMemory"]
