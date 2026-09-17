"""Sessions: a conversation tree, bound values, and a usage ledger.

A session is one :class:`~tm.core.storage.Storage` instance (entries + values +
ledger). This module adds the conversation-tree semantics on top: entries carry a
``parent_id``, the active branch is the chain from the current leaf back to the
root, and ``branch_from`` moves the leaf so later appends continue elsewhere.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter

from tm.ai.types import Message, Usage, now_ms
from tm.core.storage import Storage, StoredEntry

_MESSAGE_ADAPTER: TypeAdapter[Message] = TypeAdapter(Message)


@dataclass
class SessionEntry:
    id: str
    parent_id: str | None
    type: str
    timestamp: int
    message: Message | None = None


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


def _to_entry(stored: StoredEntry) -> SessionEntry:
    raw = stored.payload.get("message")
    message = _MESSAGE_ADAPTER.validate_python(raw) if raw is not None else None
    return SessionEntry(
        id=stored.id,
        parent_id=stored.parent_id,
        type=stored.type,
        timestamp=stored.timestamp,
        message=message,
    )


def _preview_of(message: Message | None) -> str:
    from tm.ai.types import TextContent, UserMessage

    if not isinstance(message, UserMessage):
        return ""
    if isinstance(message.content, str):
        text = message.content
    else:
        text = " ".join(b.text for b in message.content if isinstance(b, TextContent))
    return " ".join(text.split())[:60]


class Session:
    def __init__(self, path: Path, storage: Storage) -> None:
        self.path = path
        self.storage = storage
        self._leaf_id: str | None = None
        entries = storage.entries()
        if entries:
            self._leaf_id = entries[-1].id

    # -- identity ---------------------------------------------------------
    @property
    def id(self) -> str:
        return str(self.storage.header.get("id", ""))

    @property
    def name(self) -> str | None:
        return self.storage.header.get("name")

    @property
    def cwd(self) -> str | None:
        return self.storage.header.get("cwd")

    @property
    def leaf_id(self) -> str | None:
        return self._leaf_id

    # -- tree -------------------------------------------------------------
    def _entry(self, entry_id: str) -> SessionEntry | None:
        stored = self.storage.get_entry(entry_id)
        return None if stored is None else _to_entry(stored)

    def path_to(self, entry_id: str) -> list[SessionEntry]:
        chain: list[SessionEntry] = []
        current: str | None = entry_id
        while current is not None:
            entry = self._entry(current)
            if entry is None:
                break
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
        return [entry.message for entry in self.points() if entry.message is not None]

    def append(self, message: Message, parent_id: str | None = None) -> SessionEntry:
        parent = parent_id if parent_id is not None else self._leaf_id
        entry_id = uuid.uuid4().hex[:12]
        self.storage.insert_entry(
            entry_id,
            parent,
            "message",
            {"message": message.model_dump(mode="json")},
        )
        self._leaf_id = entry_id
        entry = self._entry(entry_id)
        assert entry is not None
        return entry

    def append_messages(self, messages: list[Message]) -> None:
        """Append a turn's messages (and their usage) in one atomic commit."""
        from tm.ai.types import AssistantMessage
        from tm.core.storage import EntryWrite, UsageWrite, Write

        if not messages:
            return
        writes: list[Write] = []
        parent = self._leaf_id
        last_id = parent
        for message in messages:
            entry_id = uuid.uuid4().hex[:12]
            writes.append(
                EntryWrite(
                    {
                        "id": entry_id,
                        "parent_id": parent,
                        "type": "message",
                        "payload": {"message": message.model_dump(mode="json")},
                    }
                )
            )
            if isinstance(message, AssistantMessage) and message.usage:
                writes.append(UsageWrite(usage=message.usage, entry_id=entry_id))
            parent = entry_id
            last_id = entry_id
        self.storage.commit(writes)
        self._leaf_id = last_id

    def branch_from(self, entry_id: str) -> None:
        if self.storage.get_entry(entry_id) is None:
            raise KeyError(f"Unknown session entry: {entry_id}")
        self._leaf_id = entry_id

    # -- usage ledger -----------------------------------------------------
    def add_usage(self, usage: Usage, entry_id: str | None = None) -> None:
        self.storage.insert_usage(usage, entry_id)

    def usage_totals(self) -> Usage:
        return self.storage.stats().usage


class SessionManager:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _new_path(self, session_id: str) -> Path:
        return self.directory / f"{now_ms()}-{session_id}.jsonl"

    def create(self, *, cwd: Path, name: str | None = None) -> Session:
        session_id = uuid.uuid4().hex[:12]
        path = self._new_path(session_id)
        header = {
            "v": 4,
            "kind": "header",
            "id": session_id,
            "createdAt": now_ms(),
            "cwd": str(cwd),
            "name": name,
        }
        storage = Storage.create(path, header=header)
        return Session(path, storage)

    def open(self, path: Path) -> Session:
        return Session(path, Storage.open(path))

    def fork(self, source: Session, entry_id: str, *, cwd: Path) -> Session:
        """Copy the branch up to ``entry_id`` into a new session file."""
        session_id = uuid.uuid4().hex[:12]
        path = self._new_path(session_id)
        header = {
            "v": 4,
            "kind": "header",
            "id": session_id,
            "createdAt": now_ms(),
            "cwd": str(cwd),
            "name": source.name,
            "parent_session_id": source.id,
        }
        storage = Storage.create(path, header=header)
        for entry in source.path_to(entry_id):
            if entry.message is None:
                continue
            storage.insert_entry(
                entry.id,
                entry.parent_id,
                entry.type,
                {"message": entry.message.model_dump(mode="json")},
            )
        return Session(path, storage)

    def list(self) -> list[SessionInfo]:
        if not self.directory.exists():
            return []
        infos: list[SessionInfo] = []
        for path in sorted(self.directory.glob("*.jsonl")):
            try:
                storage = Storage.open(path)
            except Exception:  # noqa: BLE001 - unreadable session files are skipped
                continue
            header = storage.header
            entries = storage.entries()
            created = int(header.get("createdAt", entries[0].timestamp if entries else 0))
            updated = entries[-1].timestamp if entries else created
            preview = ""
            for entry in entries:
                if entry.type != "message":
                    continue
                candidate = _to_entry(entry).message
                text = _preview_of(candidate)
                if text:
                    preview = text
                    break
            infos.append(
                SessionInfo(
                    id=str(header.get("id") or path.stem),
                    path=path,
                    name=header.get("name"),
                    cwd=header.get("cwd"),
                    created=created,
                    message_count=storage.stats().message_count,
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
