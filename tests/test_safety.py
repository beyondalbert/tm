from __future__ import annotations

import base64
from pathlib import Path
from typing import cast

from tm.ai.registry import Registry
from tm.cli.commands import CommandContext, SlashCommands
from tm.core.agent import Agent
from tm.host.base import Host, Identity, Resources, SystemInfo
from tm.safety import Journal, apply_undo, is_dangerous, new_change
from tm.tools.base import ToolContext
from tm.tools.edit import EditOperation, EditParams, EditTool
from tm.tools.write import WriteParams, WriteTool


class FakeHost(Host):
    name = "fake"

    def identity(self) -> Identity:
        return Identity(user="tester", elevated=False)

    def system(self) -> SystemInfo:
        return SystemInfo(os="linux", version="6", arch="x86_64", hostname="fake")

    def resources(self) -> Resources:
        return Resources(cpu_count=1, memory_total=0, memory_available=0)


def test_is_dangerous() -> None:
    assert is_dangerous("rm -rf /") is True
    assert is_dangerous("  SHUTDOWN  /t 0 ") is True
    assert is_dangerous("ls -la") is False
    assert is_dangerous("") is False


def test_journal_record_and_mark(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "changes.jsonl")
    change = new_change(
        tool="write",
        kind="file",
        target="x",
        summary="s",
        reversible=True,
        session="s1",
        undo={"path": "x", "existed": False, "content_b64": None},
    )
    journal.record(change)
    assert [entry.id for entry in journal.last(1, session="s1")] == [change.id]
    journal.mark(change.id, "reverted")
    assert journal.entries()[0].status == "reverted"


async def test_write_records_and_undo_restores(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    journal = Journal(tmp_path / "changes.jsonl")
    ctx = ToolContext(cwd=tmp_path, journal=journal, session="s1")

    await WriteTool().execute("1", WriteParams(path="a.txt", content="new"), ctx)
    assert target.read_text(encoding="utf-8") == "new"

    change = journal.last(1, session="s1")[0]
    assert change.kind == "file" and change.reversible
    apply_undo(change, FakeHost())
    assert target.read_text(encoding="utf-8") == "old"


async def test_write_undo_deletes_a_created_file(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "changes.jsonl")
    ctx = ToolContext(cwd=tmp_path, journal=journal, session="s1")
    await WriteTool().execute("1", WriteParams(path="new.txt", content="x"), ctx)

    change = journal.last(1, session="s1")[0]
    apply_undo(change, FakeHost())
    assert not (tmp_path / "new.txt").exists()


async def test_edit_undo_restores_previous_content(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hello world\n", encoding="utf-8")
    journal = Journal(tmp_path / "changes.jsonl")
    ctx = ToolContext(cwd=tmp_path, journal=journal, session="s1")

    await EditTool().execute(
        "1",
        EditParams(path="f.txt", edits=[EditOperation(old_text="world", new_text="there")]),
        ctx,
    )
    assert target.read_text(encoding="utf-8") == "hello there\n"
    apply_undo(journal.last(1, session="s1")[0], FakeHost())
    assert target.read_text(encoding="utf-8") == "hello world\n"


async def test_write_dry_run_does_not_write(tmp_path: Path) -> None:
    ctx = ToolContext(cwd=tmp_path, dry_run=True)
    result = await WriteTool().execute("1", WriteParams(path="a.txt", content="x"), ctx)
    assert "[dry-run]" in result.text()
    assert not (tmp_path / "a.txt").exists()


def test_undo_command_reverts_last_change(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("original", encoding="utf-8")
    journal = Journal(tmp_path / "changes.jsonl")
    journal.record(
        new_change(
            tool="write",
            kind="file",
            target=str(target),
            summary="write",
            reversible=True,
            session="s1",
            undo={
                "path": str(target),
                "existed": True,
                "content_b64": base64.b64encode(b"original").decode("ascii"),
            },
        )
    )
    messages: list[str] = []
    ctx = CommandContext(
        agent=cast(Agent, object()),
        registry=cast(Registry, object()),
        cwd=tmp_path,
        emit=messages.append,
        journal=journal,
    )
    SlashCommands(ctx)._undo("1")
    assert target.read_text(encoding="utf-8") == "original"
    assert messages == [f"restored {target}"]
