"""Change journal: a durable, reversible record of machine mutations.

Each mutating tool appends a :class:`Change`. A change is reversible when it
carries enough information in ``undo`` to restore the previous state; ``/undo``
replays those. Informs-audit entries with ``reversible=False`` are kept but not
replayed.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Change:
    id: str
    ts: float
    tool: str
    kind: str
    target: str
    summary: str
    reversible: bool
    session: str | None = None
    undo: dict = field(default_factory=dict)
    status: str = "applied"


def new_change(**kwargs) -> Change:
    kwargs.setdefault("id", uuid.uuid4().hex[:12])
    kwargs.setdefault("ts", time.time())
    return Change(**kwargs)


def _from_dict(data: dict) -> Change:
    undo = data.get("undo")
    return Change(
        id=str(data.get("id", "")),
        ts=float(data.get("ts", 0.0)),
        tool=str(data.get("tool", "")),
        kind=str(data.get("kind", "")),
        target=str(data.get("target", "")),
        summary=str(data.get("summary", "")),
        reversible=bool(data.get("reversible", False)),
        session=data.get("session") if isinstance(data.get("session"), str) else None,
        undo=undo if isinstance(undo, dict) else {},
        status=str(data.get("status", "applied")),
    )


class Journal:
    """Append-only JSONL log of changes, rewritten only to update status."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> list[Change]:
        if not self.path.is_file():
            return []
        changes: list[Change] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                changes.append(_from_dict(data))
        return changes

    def _write(self, changes: list[Change]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(json.dumps(asdict(change)) for change in changes)
        self.path.write_text(text + ("\n" if changes else ""), encoding="utf-8")

    def record(self, change: Change) -> Change:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(change)) + "\n")
        return change

    def entries(self, *, session: str | None = None) -> list[Change]:
        changes = self._read()
        if session is not None:
            changes = [change for change in changes if change.session == session]
        return changes

    def last(self, count: int = 1, *, session: str | None = None) -> list[Change]:
        count = max(1, count)
        return self.entries(session=session)[-count:]

    def mark(self, change_id: str, status: str) -> None:
        changes = self._read()
        for change in changes:
            if change.id == change_id:
                change.status = status
        self._write(changes)


def record_file_change(
    journal: Journal | None,
    *,
    session: str | None,
    tool: str,
    path: Path,
    existed: bool,
    content: bytes | None,
    summary: str,
) -> None:
    if journal is None:
        return
    undo = {
        "path": str(path),
        "existed": existed,
        "content_b64": (
            base64.b64encode(content).decode("ascii") if content is not None else None
        ),
    }
    journal.record(
        new_change(
            tool=tool,
            kind="file",
            target=str(path),
            summary=summary,
            reversible=not existed or content is not None,
            session=session,
            undo=undo,
        )
    )


__all__ = ["Change", "Journal", "new_change", "record_file_change"]
