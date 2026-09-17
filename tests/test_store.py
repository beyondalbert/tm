"""Tests for bound typed addresses and the state store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tm.core import store as store_mod
from tm.core.store import AddressError, Store, Value, ValueList


def _v(namespace: str, key: str = "") -> Value[Any]:
    return store_mod.value(namespace, key)


def _l(namespace: str, key: str = "") -> ValueList[Any]:
    return store_mod.AddressList(namespace, key)


def test_value_and_list_addresses_carry_namespace_and_key() -> None:
    addr: Value[Any] = _v("app.state", "k")
    assert addr.namespace == "app.state"
    assert addr.key == "k"
    assert addr.kind == "value"

    lst: ValueList[Any] = _l("app.events", "k")
    assert lst.kind == "list"
    assert isinstance(addr, Value)
    assert isinstance(lst, ValueList)


def test_empty_namespace_rejected() -> None:
    with pytest.raises(AddressError):
        _v("")


def test_separator_rejected() -> None:
    with pytest.raises(AddressError):
        _v("a\u0000b")


def test_reserved_namespace_detection() -> None:
    assert store_mod.is_reserved(_v("tm.session.name"))
    assert store_mod.is_reserved(_v("tm.op.state", "x"))
    assert not store_mod.is_reserved(_v("tmx.state"))
    assert not store_mod.is_reserved(_v("app.state"))


def test_empty_key_is_legal() -> None:
    addr: Value[Any] = _v("app.state")
    assert addr.key == ""


def test_scalar_set_get_delete_recreate() -> None:
    store = Store()
    addr: Value[Any] = _v("app.state")
    assert store.get_value(addr) is None
    store.set_value(addr, {"n": 1})
    assert store.get_value(addr) == {"n": 1}
    store.set_value(addr, {"n": 2})
    assert store.get_value(addr) == {"n": 2}
    store.delete_value(addr)
    assert store.get_value(addr) is None
    store.delete_value(addr)  # deleting an absent value is a no-op
    store.set_value(addr, "again")
    assert store.get_value(addr) == "again"


def test_sequences_are_monotonic_and_shared() -> None:
    store = Store()
    a: Value[Any] = _v("app.a")
    events: ValueList[Any] = _l("app.events")
    s1 = store.set_value(a, 1)
    s2 = store.append_list(events, "x")
    s3 = store.set_value(a, 2)
    assert s1 < s2 < s3


def test_list_append_and_cursor_paging() -> None:
    store = Store()
    events: ValueList[Any] = _l("app.events")
    for i in range(5):
        store.append_list(events, i)
    page = store.read_list(events, limit=2)
    assert [e.value for e in page] == [0, 1]
    cursor = page[-1].seq
    page2 = store.read_list(events, cursor=cursor, limit=2)
    assert [e.value for e in page2] == [2, 3]


def test_list_desc_order() -> None:
    store = Store()
    events: ValueList[Any] = _l("app.events")
    for i in range(3):
        store.append_list(events, i)
    page = store.read_list(events, order="desc", limit=2)
    assert [e.value for e in page] == [2, 1]


def test_absent_list_reads_empty() -> None:
    store = Store()
    assert store.read_list(_l("app.none")) == []


def test_delete_list_removes_everything() -> None:
    store = Store()
    events: ValueList[Any] = _l("app.events")
    store.append_list(events, 1)
    store.append_list(events, 2)
    store.delete_list(events)
    assert store.read_list(events) == []
    store.delete_list(events)  # delete of absent list is a no-op


def test_limit_validation_and_cap() -> None:
    store = Store()
    events: ValueList[Any] = _l("app.events")
    with pytest.raises(ValueError):
        store.read_list(events, limit=0)


def test_persistence_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "store.jsonl"
    store = Store.open(path)
    store.set_value(_v("app.state"), {"n": 7})
    events: ValueList[Any] = _l("app.events")
    store.append_list(events, "a")
    store.append_list(events, "b")
    store.delete_list(_l("app.gone"))

    reopened = Store.open(path)
    assert reopened.get_value(_v("app.state")) == {"n": 7}
    assert [e.value for e in reopened.read_list(events)] == ["a", "b"]


def test_replay_preserves_sequence_numbers(tmp_path: Path) -> None:
    path = tmp_path / "store.jsonl"
    store = Store.open(path)
    events: ValueList[Any] = _l("app.events")
    first_seq = store.append_list(events, "a")
    store.set_value(_v("app.other"), 1)
    second_seq = store.append_list(events, "b")

    reopened = Store.open(path)
    page = reopened.read_list(events)
    assert [e.seq for e in page] == [first_seq, second_seq]


def test_torn_final_line_is_discarded(tmp_path: Path) -> None:
    path = tmp_path / "store.jsonl"
    store = Store.open(path)
    store.set_value(_v("app.state"), 1)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind":"value","op":"set","namespace":"app.st')
    reopened = Store.open(path)
    assert reopened.get_value(_v("app.state")) == 1


def test_scan_values_filters_by_namespace_segment() -> None:
    store = Store()
    store.set_value(_v("tm.op.state", "a"), {"status": "open"})
    store.set_value(_v("tm.op.state", "b"), {"status": "open"})
    store.set_value(_v("tm.other"), 1)
    store.set_value(_v("app.state"), 2)

    found = store.scan_values("tm.op.state")
    assert {address.key for address, _ in found} == {"a", "b"}

    assert len(store.scan_values("tm")) == 3
    assert len(store.scan_values("app")) == 1
    assert len(store.scan_values("tm.op.sta")) == 0  # not a whole segment
    assert len(store.scan_values()) == 4
