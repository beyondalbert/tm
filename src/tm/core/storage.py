"""Session storage: entries, bound values/lists, and a usage ledger.

Adapted from pi's ``AgentHarness`` storage model (docs/harness.md, Part 1). One
storage instance serves one session and holds three durable forms:

* **entries** — the conversation tree: write-once, append-only, ordered by a
  session-global ``seq``.
* **values / lists** — mutable state at bound typed addresses: values replace,
  lists append (elements immutable) and delete whole.
* **usage ledger** — append-only cost rows, one per settled assistant response.

All writes go through :meth:`Storage.commit`, which assigns strictly increasing
sequence numbers and commits **all-or-none**: a crash can never leave part of a
transaction visible. The JSONL backend proves this by encoding one commit per
line (a JSON array of committed writes) and discarding a torn final line whole.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, TypeVar

from tm.ai.types import Usage, now_ms

T = TypeVar("T")

_SEPARATOR = "\u0000"
_RESERVED = "tm"


class AddressError(ValueError):
    """Raised when an address is constructed with an invalid namespace/key."""


class StorageError(RuntimeError):
    """Raised when a commit violates a storage invariant."""


# ---------------------------------------------------------------------------
# Bound typed addresses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Value(Generic[T]):
    """A bound address for one replaceable durable value of type ``T``."""

    namespace: str
    key: str = ""
    kind: str = field(default="value", init=False)

    def __post_init__(self) -> None:
        _validate(self.namespace, self.key)


@dataclass(frozen=True)
class ValueList(Generic[T]):
    """A bound address for one append-only durable list of ``T`` elements."""

    namespace: str
    key: str = ""
    kind: str = field(default="list", init=False)

    def __post_init__(self) -> None:
        _validate(self.namespace, self.key)


def value(namespace: str, key: str = "") -> Value[T]:
    return Value(namespace=namespace, key=key)


def AddressList(namespace: str, key: str = "") -> ValueList[T]:
    return ValueList(namespace=namespace, key=key)


def _validate(namespace: str, key: str) -> None:
    if not namespace:
        raise AddressError("namespace must be non-empty")
    if _SEPARATOR in namespace or _SEPARATOR in key:
        raise AddressError("namespace and key may not contain the separator")


def is_reserved(address: Value[Any] | ValueList[Any]) -> bool:
    """True if the address lives in the reserved ``tm.*`` namespace."""
    return address.namespace == _RESERVED or address.namespace.startswith(_RESERVED + ".")


# -- built-in TM addresses -------------------------------------------------


def session_name() -> Value[str]:
    return value(f"{_RESERVED}.session.name")


def session_provider() -> Value[str]:
    return value(f"{_RESERVED}.session.provider")


def session_model() -> Value[str]:
    return value(f"{_RESERVED}.session.model")


def entry_label(entry_id: str) -> Value[str]:
    return value(f"{_RESERVED}.entry.label", entry_id)


def operation_state(operation_id: str) -> Value[dict]:
    """The total durable restart point for one operation."""
    return value(f"{_RESERVED}.op.state", operation_id)


def operation_result(operation_id: str) -> Value[dict]:
    return value(f"{_RESERVED}.op.result", operation_id)


def pending_entry(entry_id: str) -> Value[dict]:
    return value(f"{_RESERVED}.pending.entry", entry_id)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass
class StoredValue(Generic[T]):
    value: T
    seq: int


@dataclass
class ListElement(Generic[T]):
    seq: int
    value: T


@dataclass
class StoredEntry:
    id: str
    parent_id: str | None
    seq: int
    timestamp: int
    type: str
    payload: dict = field(default_factory=dict)


@dataclass
class UsageRow:
    id: str
    seq: int
    usage: Usage
    entry_id: str | None = None
    adjustment: bool = False


@dataclass
class SessionStats:
    message_count: int = 0
    usage: Usage = field(default_factory=Usage)


@dataclass
class CommitResult:
    first_seq: int
    seqs: list[int]
    timestamp: int
    stats: SessionStats


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


@dataclass
class EntryWrite:
    entry: dict
    id: str = ""
    parent_id: str | None = None
    type: str = "message"
    payload: dict = field(default_factory=dict)
    kind: str = "entry"

    def __post_init__(self) -> None:
        self.id = str(self.entry["id"])
        self.parent_id = self.entry.get("parent_id")
        self.type = str(self.entry.get("type", "message"))
        self.payload = dict(self.entry.get("payload", {}))


@dataclass
class UsageWrite:
    usage: Usage
    entry_id: str | None = None
    adjustment: bool = False
    id: str = ""
    kind: str = "usage"


@dataclass
class ValueSetWrite:
    namespace: str
    key: str
    next_value: Any
    kind: str = "value"
    op: str = "set"


@dataclass
class ValueDeleteWrite:
    namespace: str
    key: str
    kind: str = "value"
    op: str = "delete"


@dataclass
class ListAppendWrite:
    namespace: str
    key: str
    value: Any
    kind: str = "list"
    op: str = "append"


@dataclass
class ListDeleteWrite:
    namespace: str
    key: str
    kind: str = "list"
    op: str = "delete"


Write = (
    EntryWrite
    | UsageWrite
    | ValueSetWrite
    | ValueDeleteWrite
    | ListAppendWrite
    | ListDeleteWrite
)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class Storage:
    """One session's storage. In-memory, optionally persisted as JSONL.

    Subclass or pass ``path`` for persistence. Writes are serialized (one
    writer) and committed all-or-none with strictly increasing sequence numbers.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        header: dict | None = None,
    ) -> None:
        self.path = path
        self.header: dict = dict(header or {})
        self._entries: dict[str, StoredEntry] = {}
        self._entry_order: list[str] = []
        self._values: dict[tuple[str, str], StoredValue[Any]] = {}
        self._lists: dict[tuple[str, str], list[ListElement[Any]]] = {}
        self._usage: list[UsageRow] = []
        self._id_counter = 0
        self._seq = 0
        self._stats = SessionStats()

    # -- lifecycle --------------------------------------------------------
    @classmethod
    def open(cls, path: Path) -> Storage:
        storage = JsonlStorage(path)
        if path.exists():
            storage._replay(path)
        return storage

    @classmethod
    def create(cls, path: Path, *, header: dict | None = None) -> Storage:
        storage = JsonlStorage(path, header=header)
        storage._write_header()
        return storage

    # -- identity ---------------------------------------------------------
    def _next_id(self) -> str:
        self._id_counter += 1
        return f"{now_ms():013d}-{self._id_counter:06d}"

    # -- commit -----------------------------------------------------------
    def commit(self, writes: list[Write]) -> CommitResult:
        """Assign sequences and apply ``writes`` atomically, all-or-none."""
        if not writes:
            raise StorageError("empty commit")
        timestamp = now_ms()
        first_seq = self._seq + 1
        committed: list[dict] = []
        seqs: list[int] = []
        new_entry_ids: set[str] = set()
        tx_ids: set[str] = set()

        for offset, write in enumerate(writes):
            seq = first_seq + offset
            record = self._commit_write(write, seq, timestamp)
            if isinstance(write, (EntryWrite, UsageWrite)):
                if write.id in self._entries or write.id in tx_ids:
                    raise StorageError(f"duplicate entry/usage id: {write.id}")
                if not write.id:
                    write.id = self._next_id()
                    record["id"] = write.id
                tx_ids.add(write.id)
            if isinstance(write, EntryWrite):
                if (
                    write.parent_id is not None
                    and write.parent_id not in self._entries
                    and write.parent_id not in new_entry_ids
                ):
                    raise StorageError(f"missing parent entry: {write.parent_id}")
                new_entry_ids.add(write.id)
            committed.append(record)
            seqs.append(seq)

        # All validation passed: apply and persist.
        for _write, record in zip(writes, committed, strict=True):
            self._apply_committed(record)
        self._seq = first_seq + len(writes) - 1
        self._persist(committed)
        return CommitResult(first_seq, seqs, timestamp, self._stats)

    def _commit_write(self, write: Write, seq: int, timestamp: int) -> dict:
        if isinstance(write, EntryWrite):
            return {
                "kind": "entry",
                "id": write.id,
                "parent_id": write.parent_id,
                "type": write.type,
                "seq": seq,
                "timestamp": timestamp,
                "payload": write.payload,
            }
        if isinstance(write, UsageWrite):
            return {
                "kind": "usage",
                "id": write.id or self._next_id(),
                "seq": seq,
                "usage": write.usage.model_dump(),
                "entry_id": write.entry_id,
                "adjustment": write.adjustment,
            }
        if isinstance(write, ValueSetWrite):
            return {
                "kind": "value",
                "op": "set",
                "namespace": write.namespace,
                "key": write.key,
                "seq": seq,
                "value": write.next_value,
            }
        if isinstance(write, ValueDeleteWrite):
            return {
                "kind": "value",
                "op": "delete",
                "namespace": write.namespace,
                "key": write.key,
                "seq": seq,
            }
        if isinstance(write, ListAppendWrite):
            return {
                "kind": "list",
                "op": "append",
                "namespace": write.namespace,
                "key": write.key,
                "seq": seq,
                "value": write.value,
            }
        return {
            "kind": "list",
            "op": "delete",
            "namespace": write.namespace,
            "key": write.key,
            "seq": seq,
        }

    def _apply_committed(self, record: dict) -> None:
        kind = record.get("kind")
        if kind == "entry":
            entry = StoredEntry(
                id=str(record["id"]),
                parent_id=record.get("parent_id"),
                seq=int(record["seq"]),
                timestamp=int(record["timestamp"]),
                type=str(record.get("type", "message")),
                payload=dict(record.get("payload", {})),
            )
            if entry.id not in self._entries:
                self._entry_order.append(entry.id)
            self._entries[entry.id] = entry
            if entry.type == "message":
                self._stats.message_count += 1
        elif kind == "usage":
            row = UsageRow(
                id=str(record["id"]),
                seq=int(record["seq"]),
                usage=Usage.model_validate(record.get("usage") or {}),
                entry_id=record.get("entry_id"),
                adjustment=bool(record.get("adjustment", False)),
            )
            self._usage.append(row)
            self._stats.usage = _add_usage(self._stats.usage, row.usage)
        elif kind == "value":
            address = (str(record.get("namespace", "")), str(record.get("key", "")))
            if record.get("op") == "set":
                self._values[address] = StoredValue(record.get("value"), int(record["seq"]))
            else:
                self._values.pop(address, None)
        elif kind == "list":
            address = (str(record.get("namespace", "")), str(record.get("key", "")))
            if record.get("op") == "append":
                self._lists.setdefault(address, []).append(
                    ListElement(int(record["seq"]), record.get("value"))
                )
            else:
                self._lists.pop(address, None)

    # -- persistence hooks -------------------------------------------------
    def _persist(self, committed: list[dict]) -> None:
        return None

    def _write_header(self) -> None:
        return None

    # -- queries: entries --------------------------------------------------
    def entries(self) -> list[StoredEntry]:
        return [self._entries[eid] for eid in self._entry_order]

    def get_entry(self, entry_id: str) -> StoredEntry | None:
        return self._entries.get(entry_id)

    def scan_entries(
        self, *, type: str | None = None, limit: int = 10_000
    ) -> list[StoredEntry]:
        rows = self.entries()
        if type is not None:
            rows = [row for row in rows if row.type == type]
        return rows[:limit]

    # -- queries: values/lists --------------------------------------------
    def get_value(self, address: Value[T]) -> T | None:
        stored = self._values.get((address.namespace, address.key))
        return None if stored is None else stored.value

    def scan_values(self, namespace_prefix: str = "") -> list[tuple[Value[Any], Any]]:
        out: list[tuple[Value[Any], Any]] = []
        for (namespace, key), stored in sorted(self._values.items()):
            if (
                not namespace_prefix
                or namespace == namespace_prefix
                or namespace.startswith(namespace_prefix + ".")
            ):
                out.append((value(namespace, key), stored.value))
        return out

    def read_list(
        self,
        address: ValueList[T],
        *,
        cursor: int | None = None,
        order: str = "asc",
        limit: int = 1000,
    ) -> list[ListElement[T]]:
        if limit < 1:
            raise ValueError("limit must be a positive integer")
        limit = min(limit, 10_000)
        elements = self._lists.get((address.namespace, address.key), [])
        if cursor is not None:
            if order == "desc":
                elements = [e for e in elements if e.seq < cursor]
            else:
                elements = [e for e in elements if e.seq > cursor]
        ordered = sorted(elements, key=lambda e: e.seq, reverse=order == "desc")
        return ordered[:limit]

    # -- queries: usage ----------------------------------------------------
    def scan_usage(self) -> list[UsageRow]:
        return list(self._usage)

    def stats(self) -> SessionStats:
        return SessionStats(
            message_count=self._stats.message_count,
            usage=self._stats.usage.model_copy(),
        )

    # -- value writes ------------------------------------------------------
    def set_value(self, address: Value[T], next_value: T) -> int:
        return self.commit([ValueSetWrite(address.namespace, address.key, next_value)]).first_seq

    def delete_value(self, address: Value[T]) -> None:
        if (address.namespace, address.key) not in self._values:
            return
        self.commit([ValueDeleteWrite(address.namespace, address.key)])

    # -- list writes -------------------------------------------------------
    def append_list(self, address: ValueList[T], element: T) -> int:
        return self.commit(
            [ListAppendWrite(address.namespace, address.key, element)]
        ).first_seq

    def delete_list(self, address: ValueList[T]) -> None:
        if (address.namespace, address.key) not in self._lists:
            return
        self.commit([ListDeleteWrite(address.namespace, address.key)])

    # -- entry/usage writes ------------------------------------------------
    def insert_entry(
        self,
        entry_id: str,
        parent_id: str | None,
        type: str,
        payload: dict,
    ) -> StoredEntry:
        self.commit([EntryWrite({"id": entry_id, "parent_id": parent_id, "type": type, "payload": payload})])
        result = self._entries[entry_id]
        return result

    def insert_usage(self, usage: Usage, entry_id: str | None = None) -> int:
        return self.commit([UsageWrite(usage=usage, entry_id=entry_id)]).first_seq

    def close(self) -> None:
        return None


def _add_usage(base: Usage, extra: Usage) -> Usage:
    return Usage(
        input=base.input + extra.input,
        output=base.output + extra.output,
        cache_read=base.cache_read + extra.cache_read,
        cache_write=base.cache_write + extra.cache_write,
        total=base.total + extra.total,
    )


class JsonlStorage(Storage):
    """JSONL backend: one commit per line, torn final line discarded whole."""

    _VERSION = 4

    def _persist(self, committed: list[dict]) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(committed, ensure_ascii=False) + "\n")

    def _write_header(self) -> None:
        if self.path is None or (self.path.exists() and self.path.stat().st_size > 0):
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.header, ensure_ascii=False) + "\n", encoding="utf-8")

    def _replay(self, path: Path) -> None:
        data = path.read_bytes()
        offset = 0
        good_offset = 0
        lines = data.split(b"\n")
        for index, raw in enumerate(lines):
            last = index == len(lines) - 1
            offset += len(raw) + 1
            if not raw.strip():
                if last:
                    good_offset = offset - 1
                continue
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                if last:
                    break  # torn final line: discard whole
                raise StorageError(f"corrupt storage line in {path}") from None
            if isinstance(parsed, dict) and parsed.get("kind") == "header":
                self.header = parsed
                self._id_counter = 0
            elif isinstance(parsed, list):
                for record in parsed:
                    self._apply_committed(record)
                    self._seq = max(self._seq, int(record.get("seq", 0)))
            elif isinstance(parsed, dict):
                self._apply_legacy_or_write(parsed)
            good_offset = offset
        if good_offset < len(data):
            with path.open("r+b") as handle:
                handle.truncate(good_offset)

    def _apply_legacy_or_write(self, record: dict) -> None:
        if "kind" in record:
            self._apply_committed(record)
            self._seq = max(self._seq, int(record.get("seq", 0)))
            return
        # Legacy v3 session line: {"id","parent_id","type","timestamp","message"|"name"|"cwd"}
        entry_type = str(record.get("type", "message"))
        if entry_type == "meta":
            self.header.setdefault("id", record.get("id"))
            if record.get("name") is not None:
                self.header.setdefault("name", record["name"])
            if record.get("cwd") is not None:
                self.header.setdefault("cwd", record["cwd"])
            return
        payload: dict = {}
        if "message" in record:
            payload = {"message": record["message"]}
        self._seq += 1
        self._apply_committed(
            {
                "kind": "entry",
                "id": str(record["id"]),
                "parent_id": record.get("parent_id"),
                "type": entry_type,
                "seq": self._seq,
                "timestamp": int(record.get("timestamp", now_ms())),
                "payload": payload,
            }
        )


# Backwards-compatible name: tm.core.store.Store == tm.core.storage.Storage.
Store = Storage


__all__ = [
    "AddressError",
    "AddressList",
    "CommitResult",
    "EntryWrite",
    "JsonlStorage",
    "ListAppendWrite",
    "ListDeleteWrite",
    "ListElement",
    "SessionStats",
    "Storage",
    "StorageError",
    "Store",
    "StoredEntry",
    "StoredValue",
    "UsageRow",
    "UsageWrite",
    "Value",
    "ValueDeleteWrite",
    "ValueList",
    "ValueSetWrite",
    "Write",
    "entry_label",
    "is_reserved",
    "operation_result",
    "operation_state",
    "pending_entry",
    "session_model",
    "session_name",
    "session_provider",
    "value",
]
