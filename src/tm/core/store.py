"""Compatibility facade: storage now lives in :mod:`tm.core.storage`.

Kept so existing imports (``tm.core.store``) and tests keep working; the
implementation, including the three-store model, is in ``tm.core.storage``.
"""

from tm.core.storage import (
    AddressError,
    AddressList,
    CommitResult,
    JsonlStorage,
    ListElement,
    SessionStats,
    Storage,
    StorageError,
    Store,
    StoredEntry,
    StoredValue,
    UsageRow,
    Value,
    ValueList,
    entry_label,
    is_reserved,
    operation_result,
    operation_state,
    pending_entry,
    session_name,
    value,
)

__all__ = [
    "AddressError",
    "AddressList",
    "CommitResult",
    "JsonlStorage",
    "ListElement",
    "SessionStats",
    "Storage",
    "StorageError",
    "Store",
    "StoredEntry",
    "StoredValue",
    "UsageRow",
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
