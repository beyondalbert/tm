"""JSONL session persistence with branching.

Sessions are append-only JSONL files. Each entry points at its parent, so a
single file can hold a branching conversation tree. The active branch is the
chain of entries from the current leaf back to the root; ``branch_from`` moves
the leaf so later messages continue from another point.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from tm.ai.types import Message, now_ms

_MESSAGE_ADAPTER: TypeAdapter[Message] = TypeAdapter(Message)


class SessionEntry(BaseModel):
    id: str
    parent_id: str | None = None
    type: str
    timestamp: int
    message: dict | None = None
    name: str | None = None
    cwd: str | None = None


@dataclass
class SessionInfo:
    id: str
    path: Path
    name: str | None
    cwd: str | None
    created: int
    message_count: int = 0
    updated: int = 0
    preview: str = ""


def _preview_of(message: dict | None) -> str:
    if not isinstance(message, dict) or message.get("role") != "user":
        return ""
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    else:
        text = ""
    text = " ".join(text.split())
    return text[:60]


class Session:
    def __init__(self, path: Path, entries: list[SessionEntry] | None = None) -> None:
        self.path = path
        self._entries: list[SessionEntry] = list(entries or [])
        self._leaf_id: str | None = self._entries[-1].id if self._entries else None

    @property
    def id(self) -> str:
        return self._entries[0].id if self._entries else ""

    @property
    def name(self) -> str | None:
        return self._entries[0].name if self._entries else None

    @property
    def cwd(self) -> str | None:
        return self._entries[0].cwd if self._entries else None

    @property
    def leaf_id(self) -> str | None:
        return self._leaf_id

    def _by_id(self) -> dict[str, SessionEntry]:
        return {entry.id: entry for entry in self._entries}

    def path_to(self, entry_id: str) -> list[SessionEntry]:
        by_id = self._by_id()
        chain: list[SessionEntry] = []
        current: str | None = entry_id
        while current is not None and current in by_id:
            entry = by_id[current]
            chain.append(entry)
            current = entry.parent_id
        chain.reverse()
        return chain

    def active_entries(self) -> list[SessionEntry]:
        if self._leaf_id is None:
            return []
        return self.path_to(self._leaf_id)

    def points(self) -> list[SessionEntry]:
        """Message entries on the active branch, oldest first."""
        return [
            entry
            for entry in self.active_entries()
            if entry.type == "message" and entry.message is not None
        ]

    def messages(self) -> list[Message]:
        return [
            _MESSAGE_ADAPTER.validate_python(entry.message)
            for entry in self.points()
            if entry.message is not None
        ]

    def append(self, message: Message, parent_id: str | None = None) -> SessionEntry:
        parent = parent_id if parent_id is not None else self._leaf_id
        entry = SessionEntry(
            id=uuid.uuid4().hex[:12],
            parent_id=parent,
            type="message",
            timestamp=now_ms(),
            message=message.model_dump(mode="json"),
        )
        self._entries.append(entry)
        self._leaf_id = entry.id
        self._write_entry(entry)
        return entry

    def branch_from(self, entry_id: str) -> None:
        if entry_id not in self._by_id():
            raise KeyError(f"Unknown session entry: {entry_id}")
        self._leaf_id = entry_id

    def _write_entry(self, entry: SessionEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(entry.model_dump_json() + "\n")


class SessionManager:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def create(self, *, cwd: Path, name: str | None = None) -> Session:
        session_id = uuid.uuid4().hex[:12]
        path = self.directory / f"{now_ms()}-{session_id}.jsonl"
        meta = SessionEntry(
            id=session_id,
            type="meta",
            timestamp=now_ms(),
            name=name,
            cwd=str(cwd),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(meta.model_dump_json() + "\n", encoding="utf-8")
        return Session(path, [meta])

    def open(self, path: Path) -> Session:
        entries: list[SessionEntry] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    entries.append(SessionEntry.model_validate_json(line))
        return Session(path, entries)

    def fork(self, source: Session, entry_id: str, *, cwd: Path) -> Session:
        """Create a new session file containing the branch up to ``entry_id``."""
        session_id = uuid.uuid4().hex[:12]
        path = self.directory / f"{now_ms()}-{session_id}.jsonl"
        meta = SessionEntry(
            id=session_id,
            type="meta",
            timestamp=now_ms(),
            name=source.name,
            cwd=str(cwd),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(meta.model_dump_json() + "\n", encoding="utf-8")

        entries = [meta]
        with path.open("a", encoding="utf-8") as handle:
            for entry in source.path_to(entry_id):
                if entry.type == "meta":
                    continue
                copied = entry.model_copy(deep=True)
                entries.append(copied)
                handle.write(copied.model_dump_json() + "\n")
        return Session(path, entries)

    def list(self) -> list[SessionInfo]:
        if not self.directory.exists():
            return []
        infos: list[SessionInfo] = []
        for path in sorted(self.directory.glob("*.jsonl")):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    lines = [line.strip() for line in handle if line.strip()]
                if not lines:
                    continue
                meta = SessionEntry.model_validate_json(lines[0])
            except (OSError, ValueError):
                continue

            count = 0
            updated = meta.timestamp
            preview = ""
            for line in lines[1:]:
                try:
                    entry = SessionEntry.model_validate_json(line)
                except ValueError:
                    continue
                if entry.type != "message":
                    continue
                count += 1
                updated = entry.timestamp
                if not preview:
                    preview = _preview_of(entry.message)
            infos.append(
                SessionInfo(
                    id=meta.id,
                    path=path,
                    name=meta.name,
                    cwd=meta.cwd,
                    created=meta.timestamp,
                    message_count=count,
                    updated=updated,
                    preview=preview,
                )
            )
        return infos

    def continue_recent(self, cwd: Path) -> Session | None:
        matching = [info for info in self.list() if info.cwd == str(cwd)]
        if not matching:
            return None
        latest = max(matching, key=lambda info: info.created)
        return self.open(latest.path)


__all__ = ["Session", "SessionEntry", "SessionInfo", "SessionManager"]
