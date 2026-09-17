"""Bound typed addresses and a small durable state store.

Adapted from Pi's ``values.md``. The idea: a durable piece of mutable state is
named by a *bound typed address* constructed once with ``value()`` or
``list()``. Every later read or write receives only that address; the namespace,
key, and value type never have to be repeated. There is no global type registry,
declaration merging, or separate application-state mechanism.

Two kinds of address:

* :func:`value` names one replaceable value. ``set`` replaces, ``delete`` clears.
* :func:`AddressList` names one append-only list. Elements are immutable and
  ordered by a session-global write ``seq``; only whole-list deletion exists.

The :class:`Store` is an in-memory backend with deterministic JSONL persistence,
mirroring TM's session file. It is deliberately small: it stores scalars and
lists and assigns sequence numbers. It knows nothing about agents or messages.

Addresses reserved for TM built-ins use the ``tm.`` namespace prefix.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, TypeVar

T = TypeVar("T")

_SEPARATOR = "\u0000"
_RESERVED = "tm"


class AddressError(ValueError):
    """Raised when an address is constructed with an invalid namespace/key."""


@dataclass(frozen=True)
class Value(Generic[T]):
    """A bound address for one replaceable durable value of type ``T``.

    The type parameter is documentation and static-analysis only; Python does
    not enforce it at runtime, matching the trusted-in-process model.
    """

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
    """Construct a bound address for one replaceable value."""
    return Value(namespace=namespace, key=key)


def AddressList(namespace: str, key: str = "") -> ValueList[T]:
    """Construct a bound address for one append-only list."""
    return ValueList(namespace=namespace, key=key)


def _validate(namespace: str, key: str) -> None:
    if not namespace:
        raise AddressError("namespace must be non-empty")
    if _SEPARATOR in namespace or _SEPARATOR in key:
        raise AddressError("namespace and key may not contain the separator")


def is_reserved(address: Value[Any] | ValueList[Any]) -> bool:
    """True if the address lives in the reserved ``tm.*`` namespace."""
    return address.namespace == _RESERVED or address.namespace.startswith(_RESERVED + ".")


# ---------------------------------------------------------------------------
# Built-in TM addresses
# ---------------------------------------------------------------------------


def session_name() -> Value[str]:
    return value(f"{_RESERVED}.session.name")


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
# Store
# ---------------------------------------------------------------------------


@dataclass
class StoredValue(Generic[T]):
    value: T
    seq: int


@dataclass
class ListElement(Generic[T]):
    seq: int
    value: T


class Store:
    """In-memory scalar/list store with JSONL persistence.

    Scalar writes replace; list appends are immutable and ordered by a
    session-global monotonically increasing ``seq``. A ``list`` delete removes
    every element at one address. Reads of lists are bounded by an exclusive
    ``seq`` cursor and a positive ``limit`` (default 1000, capped at 10000).
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._values: dict[tuple[str, str], StoredValue[Any]] = {}
        self._lists: dict[tuple[str, str], list[ListElement[Any]]] = {}
        self._seq = 0

    # -- persistence ------------------------------------------------------
    @classmethod
    def open(cls, path: Path) -> Store:
        store = cls(path)
        if path.exists():
            store._replay(path)
        return store

    def _replay(self, path: Path) -> None:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    # A torn final line is discarded whole; interior corruption
                    # is unsupported by contract.
                    break
                self._apply(record)

    def _apply(self, record: dict) -> None:
        kind = record.get("kind")
        op = record.get("op")
        namespace = str(record.get("namespace", ""))
        key = str(record.get("key", ""))
        seq = int(record.get("seq", 0))
        self._seq = max(self._seq, seq)
        address = (namespace, key)
        if kind == "value":
            if op == "set":
                self._values[address] = StoredValue(record.get("value"), seq)
            elif op == "delete":
                self._values.pop(address, None)
        elif kind == "list":
            if op == "append":
                self._lists.setdefault(address, []).append(ListElement(seq, record.get("value")))
            elif op == "delete":
                self._lists.pop(address, None)

    def _persist(self, records: list[dict]) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    # -- values -----------------------------------------------------------
    def get_value(self, address: Value[T]) -> T | None:
        stored = self._values.get((address.namespace, address.key))
        return None if stored is None else stored.value

    def set_value(self, address: Value[T], next_value: T) -> int:
        seq = self._next_seq()
        record = {
            "kind": "value",
            "op": "set",
            "namespace": address.namespace,
            "key": address.key,
            "seq": seq,
            "value": next_value,
        }
        self._apply(record)
        self._persist([record])
        return seq

    def delete_value(self, address: Value[T]) -> None:
        if (address.namespace, address.key) not in self._values:
            return
        record = {
            "kind": "value",
            "op": "delete",
            "namespace": address.namespace,
            "key": address.key,
            "seq": self._next_seq(),
        }
        self._apply(record)
        self._persist([record])

    def scan_values(self, namespace_prefix: str = "") -> list[tuple[Value[Any], Any]]:
        """Return ``(address, value)`` for values matching a namespace prefix.

        An empty prefix returns everything. The prefix matches whole segments, so
        ``tm.op`` matches ``tm.op`` and ``tm.op.state`` but not ``tm.ops``.
        """
        out: list[tuple[Value[Any], Any]] = []
        for (namespace, key), stored in sorted(self._values.items()):
            matches = (
                not namespace_prefix
                or namespace == namespace_prefix
                or namespace.startswith(namespace_prefix + ".")
            )
            if matches:
                out.append((value(namespace, key), stored.value))
        return out

    # -- lists ------------------------------------------------------------
    def append_list(self, address: ValueList[T], element: T) -> int:
        seq = self._next_seq()
        record = {
            "kind": "list",
            "op": "append",
            "namespace": address.namespace,
            "key": address.key,
            "seq": seq,
            "value": element,
        }
        self._apply(record)
        self._persist([record])
        return seq

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
        limit = min(limit, 10000)
        elements = self._lists.get((address.namespace, address.key), [])
        if cursor is not None:
            if order == "desc":
                elements = [e for e in elements if e.seq < cursor]
            else:
                elements = [e for e in elements if e.seq > cursor]
        ordered = sorted(elements, key=lambda e: e.seq, reverse=order == "desc")
        return ordered[:limit]

    def delete_list(self, address: ValueList[T]) -> None:
        if (address.namespace, address.key) not in self._lists:
            return
        record = {
            "kind": "list",
            "op": "delete",
            "namespace": address.namespace,
            "key": address.key,
            "seq": self._next_seq(),
        }
        self._apply(record)
        self._persist([record])


__all__ = [
    "AddressError",
    "AddressList",
    "ListElement",
    "Store",
    "StoredValue",
    "Value",
    "ValueList",
    "entry_label",
    "is_reserved",
    "operation_result",
    "operation_state",
    "pending_entry",
    "session_name",
    "value",
]
