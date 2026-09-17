"""Tests for the session storage model: atomic commits, ledger, and queries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tm.ai.event_stream import EventStream
from tm.ai.types import (
    AssistantMessage,
    DoneEvent,
    Model,
    StartEvent,
    TextContent,
    Usage,
)
from tm.core.agent import Agent
from tm.core.session import SessionManager
from tm.core.storage import (
    EntryWrite,
    ListAppendWrite,
    Storage,
    StorageError,
    UsageWrite,
    ValueSetWrite,
)
from tm.core.store import operation_result

MODEL = Model(id="fake", provider="fake")


def _entry(entry_id: str, parent: str | None, text: str) -> EntryWrite:
    return EntryWrite(
        {
            "id": entry_id,
            "parent_id": parent,
            "type": "message",
            "payload": {"message": {"role": "user", "content": text}},
        }
    )


def test_commit_is_all_or_none_on_invalid_parent() -> None:
    storage = Storage()
    storage.commit([_entry("a", None, "one")])
    seq_before = storage.entries()[-1].seq

    with pytest.raises(StorageError):
        storage.commit([_entry("b", None, "two"), _entry("c", "missing", "three")])

    assert [entry.id for entry in storage.entries()] == ["a"]
    # nothing from the failed commit is visible or sequenced
    assert storage.entries()[-1].seq == seq_before


def test_commit_assigns_increasing_sequences() -> None:
    storage = Storage()
    result = storage.commit(
        [
            _entry("a", None, "one"),
            UsageWrite(usage=Usage(input=1, output=1, total=2), entry_id="a"),
            ValueSetWrite("app", "k", 1),
            ListAppendWrite("app.list", "", "x"),
        ]
    )
    assert result.seqs == sorted(result.seqs)
    assert len(set(result.seqs)) == 4
    assert result.stats.message_count == 1


def test_duplicate_id_is_rejected() -> None:
    storage = Storage()
    storage.commit([_entry("a", None, "one")])
    with pytest.raises(StorageError):
        storage.commit([_entry("a", None, "again")])


def test_usage_ledger_totals_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    storage = Storage.create(path, header={"v": 4, "kind": "header", "id": "s1"})
    storage.commit(
        [
            _entry("a", None, "one"),
            UsageWrite(usage=Usage(input=10, output=5, total=15), entry_id="a"),
            _entry("b", "a", "two"),
            UsageWrite(usage=Usage(input=3, output=2, cache_read=1, total=5), entry_id="b"),
        ]
    )
    assert storage.stats().usage.total == 20
    assert storage.stats().message_count == 2

    reopened = Storage.open(path)
    assert reopened.stats().usage.total == 20
    assert reopened.stats().usage.cache_read == 1
    assert [row.entry_id for row in reopened.scan_usage()] == ["a", "b"]


def test_torn_final_commit_is_discarded_whole(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    storage = Storage.create(path, header={"v": 4, "kind": "header", "id": "s1"})
    storage.commit([_entry("a", None, "one"), _entry("b", "a", "two")])

    # A crash mid-commit leaves a partial JSON array line.
    with path.open("a", encoding="utf-8") as handle:
        handle.write('[{"kind": "entry", "id": "c", "parent_id": "b"')

    reopened = Storage.open(path)
    assert [entry.id for entry in reopened.entries()] == ["a", "b"]

    # A later commit appends cleanly after the truncation.
    reopened.commit([_entry("d", "b", "four")])
    again = Storage.open(path)
    assert [entry.id for entry in again.entries()] == ["a", "b", "d"]


def test_legacy_v3_session_file_is_readable(tmp_path: Path) -> None:
    path = tmp_path / "legacy.jsonl"
    lines = [
        {"id": "s1", "parent_id": None, "type": "meta", "timestamp": 1, "name": "old", "cwd": "C:/w"},
        {"id": "m1", "parent_id": "s1", "type": "message", "timestamp": 2, "message": {"role": "user", "content": "hi"}},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    manager = SessionManager(tmp_path)
    session = manager.open(path)
    assert session.name == "old"
    assert session.cwd == "C:/w"
    assert [m.content for m in session.messages()] == ["hi"]


def test_operation_state_lives_in_session_storage(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path)
    agent = Agent(MODEL, stream_fn=_final("done"), session=session)

    import asyncio

    asyncio.run(agent.prompt("hi"))

    assert agent.operation is not None
    result = session.storage.get_value(operation_result(agent.operation.operation_id))
    assert result is not None and result["status"] == "completed"


def _final(text: str):
    def stream_fn(model, context, options) -> EventStream:
        message = AssistantMessage(
            content=[TextContent(text=text)],
            stop_reason="stop",
            usage=Usage(input=7, output=3, total=10),
        )
        stream: EventStream = EventStream()
        stream.push(StartEvent(partial=message))
        stream.push(DoneEvent(partial=message, message=message))
        stream.end(message)
        return stream

    return stream_fn


def test_agent_records_usage_in_ledger(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path)
    agent = Agent(MODEL, stream_fn=_final("done"), session=session)

    import asyncio

    asyncio.run(agent.prompt("hi"))

    assert session.usage_totals().total == 10
    assert session.usage_totals().input == 7
    # the turn (user + assistant) is one atomic commit
    roles = [m.role for m in manager.open(session.path).messages()]
    assert roles == ["user", "assistant"]
