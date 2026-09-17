from __future__ import annotations

import sys
from pathlib import Path

from tm.tools.base import ToolContext
from tm.tools.python import PythonParams, PythonTool, script_path


def make_tool(
    tmp_path: Path, *, auto_install: bool = False, timeout: int = 10
) -> PythonTool:
    return PythonTool(
        executable=sys.executable,
        workspace=tmp_path / "ws",
        auto_install=auto_install,
        timeout=timeout,
    )


async def test_python_runs_and_saves_script(tmp_path: Path) -> None:
    tool = make_tool(tmp_path)
    result = await tool.execute(
        "1", PythonParams(code="print('hi from python')"), ToolContext(cwd=tmp_path)
    )
    assert result.is_error is False
    assert "hi from python" in result.text()
    assert "exit code 0" in result.text()

    scripts = list((tmp_path / "ws" / "scripts").glob("*.py"))
    assert len(scripts) == 1
    assert scripts[0].read_text() == "print('hi from python')"


async def test_python_reports_traceback(tmp_path: Path) -> None:
    tool = make_tool(tmp_path)
    result = await tool.execute(
        "1",
        PythonParams(code="raise ValueError('boom')", description="raise an error"),
        ToolContext(cwd=tmp_path),
    )
    assert result.is_error is True
    assert "boom" in result.text()
    assert "exit code 1" in result.text()


async def test_python_timeout_is_reported(tmp_path: Path) -> None:
    tool = make_tool(tmp_path)
    result = await tool.execute(
        "1",
        PythonParams(code="import time\ntime.sleep(10)", timeout=1),
        ToolContext(cwd=tmp_path),
    )
    assert result.is_error is True
    assert "timed out" in result.text()


async def test_python_runs_in_requested_cwd(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    tool = make_tool(tmp_path)
    result = await tool.execute(
        "1",
        PythonParams(code="import os\nprint(os.getcwd())", cwd=str(work)),
        ToolContext(cwd=tmp_path),
    )
    assert result.is_error is False
    assert str(work) in result.text()


async def test_python_already_installed_package_is_skipped(tmp_path: Path) -> None:
    tool = make_tool(tmp_path, auto_install=True)
    result = await tool.execute(
        "1",
        PythonParams(code="print('ok')", packages=["json"]),
        ToolContext(cwd=tmp_path),
    )
    assert result.is_error is False
    assert "ok" in result.text()


async def test_script_path_is_deterministic_and_named(tmp_path: Path) -> None:
    first = script_path(tmp_path, "List big files", "print(1)")
    again = script_path(tmp_path, "List big files", "print(1)")
    assert first == again
    assert first.name.startswith("list-big-files-")
    assert first.suffix == ".py"
