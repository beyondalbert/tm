from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tm.tools.base import ToolContext
from tm.tools.edit import EditOperation, EditParams, EditTool
from tm.tools.find import FindParams, FindTool
from tm.tools.grep import GrepParams, GrepTool
from tm.tools.ls import LsParams, LsTool
from tm.tools.read import ReadParams, ReadTool
from tm.tools.shell import ShellParams, ShellTool
from tm.tools.write import WriteParams, WriteTool


def ctx(root: Path) -> ToolContext:
    return ToolContext(cwd=root)


async def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    tool = WriteTool()
    result = await tool.execute(
        "1", WriteParams(path="sub/hello.txt", content="one\ntwo\n"), ctx(tmp_path)
    )
    assert result.is_error is False
    assert (tmp_path / "sub" / "hello.txt").read_text() == "one\ntwo\n"

    read = await ReadTool().execute("2", ReadParams(path="sub/hello.txt"), ctx(tmp_path))
    assert read.is_error is False
    assert read.text() == "     1: one\n     2: two"


async def test_read_missing_and_binary(tmp_path: Path) -> None:
    missing = await ReadTool().execute("1", ReadParams(path="nope.txt"), ctx(tmp_path))
    assert missing.is_error is True

    (tmp_path / "bin.dat").write_bytes(b"\x00\x01\x02")
    binary = await ReadTool().execute("2", ReadParams(path="bin.dat"), ctx(tmp_path))
    assert binary.is_error is True
    assert "Binary" in binary.text()


async def test_read_offset_and_limit(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("a\nb\nc\nd\n")
    result = await ReadTool().execute(
        "1", ReadParams(path="f.txt", offset=2, limit=2), ctx(tmp_path)
    )
    assert result.text() == "     2: b\n     3: c"


async def test_edit_applies_unique_replacement(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hello world\n")
    result = await EditTool().execute(
        "1",
        EditParams(path="f.txt", edits=[EditOperation(old_text="world", new_text="there")]),
        ctx(tmp_path),
    )
    assert result.is_error is False
    assert target.read_text() == "hello there\n"


async def test_edit_rejects_ambiguous_and_missing(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("a\na\n")
    ambiguous = await EditTool().execute(
        "1",
        EditParams(path="f.txt", edits=[EditOperation(old_text="a", new_text="b")]),
        ctx(tmp_path),
    )
    assert ambiguous.is_error is True
    assert "matches 2 times" in ambiguous.text()

    missing = await EditTool().execute(
        "2",
        EditParams(path="f.txt", edits=[EditOperation(old_text="zzz", new_text="b")]),
        ctx(tmp_path),
    )
    assert missing.is_error is True


async def test_ls_marks_directories(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    result = await LsTool().execute("1", LsParams(), ctx(tmp_path))
    assert result.text().splitlines() == ["a.txt", "sub/"]


async def test_shell_runs_command(tmp_path: Path) -> None:
    result = await ShellTool().execute("1", ShellParams(command="echo hello-tm"), ctx(tmp_path))
    assert result.is_error is False
    assert "hello-tm" in result.text()
    assert "exit code 0" in result.text()


async def test_shell_timeout_is_reported(tmp_path: Path) -> None:
    command = "Start-Sleep -Seconds 10" if os.name == "nt" else "sleep 10"
    result = await ShellTool().execute(
        "1", ShellParams(command=command, timeout=1), ctx(tmp_path)
    )
    assert result.is_error is True
    assert "timed out" in result.text()


async def test_shell_nonzero_exit_is_an_error(tmp_path: Path) -> None:
    result = await ShellTool().execute("1", ShellParams(command="exit 3"), ctx(tmp_path))
    assert result.is_error is True
    assert "exit code 3" in result.text()


async def test_grep_python_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: None)
    (tmp_path / "a.txt").write_text("alpha\nneedle here\n")
    (tmp_path / "b.txt").write_text("nothing\n")
    result = await GrepTool().execute("1", GrepParams(pattern="needle"), ctx(tmp_path))
    assert result.is_error is False
    assert "needle here" in result.text()
    assert "a.txt" in result.text()


async def test_grep_no_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: None)
    (tmp_path / "a.txt").write_text("alpha\n")
    result = await GrepTool().execute("1", GrepParams(pattern="zzz"), ctx(tmp_path))
    assert "No matches" in result.text()


async def test_grep_truncates_long_match_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: None)
    (tmp_path / "big.txt").write_text("needle " + "x" * 1000 + "\n")
    result = await GrepTool().execute("1", GrepParams(pattern="needle"), ctx(tmp_path))
    text = result.text()
    assert "needle" in text
    assert "[truncated]" in text
    assert "x" * 600 not in text


async def test_find_python_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: None)
    (tmp_path / "foo.txt").write_text("x")
    (tmp_path / "bar.md").write_text("x")
    result = await FindTool().execute("1", FindParams(pattern="*.txt"), ctx(tmp_path))
    assert result.is_error is False
    assert "foo.txt" in result.text()
    assert "bar.md" not in result.text()


async def test_shell_truncation_keeps_the_error_at_the_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TM_CONFIG_DIR", str(tmp_path / "cfg"))
    if os.name == "nt":
        command = (
            '1..2500 | ForEach-Object { Write-Output "line $_" }; '
            'Write-Output "FINAL-ERROR-MARKER"'
        )
    else:
        command = "seq 2500 | sed 's/^/line /'; echo FINAL-ERROR-MARKER"
    result = await ShellTool().execute("1", ShellParams(command=command), ctx(tmp_path))
    text = result.text()
    assert "FINAL-ERROR-MARKER" in text
    assert "line 1\n" not in text
    assert "Showing lines" in text
    assert "Full output:" in text


async def test_read_reports_continuation_offset(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text(
        "\n".join(f"row {i}" for i in range(1, 2201)) + "\n"
    )
    result = await ReadTool().execute("1", ReadParams(path="big.txt"), ctx(tmp_path))
    text = result.text()
    assert "row 1" in text
    assert "row 2200" not in text
    assert "Use offset=2001 to continue." in text
