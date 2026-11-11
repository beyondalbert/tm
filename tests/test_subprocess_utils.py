from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
import time
from typing import cast

import pytest

from tm.tools.subprocess_utils import kill_process_tree, run_capture, stream_command


class _FakeProcess:
    def __init__(self, pid: int = 0) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.waited = False

    async def wait(self) -> int:
        self.waited = True
        self.returncode = 0
        return 0


async def test_kill_process_tree_awaits_the_killer(monkeypatch) -> None:
    if os.name != "nt":
        pytest.skip("taskkill is Windows-only")
    created: list[_FakeProcess] = []

    async def fake_exec(*args: str, **kwargs: object) -> _FakeProcess:
        process = _FakeProcess(pid=1)
        created.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    target = _FakeProcess(pid=42)

    await kill_process_tree(cast(asyncio.subprocess.Process, target))

    assert target.returncode == 0
    assert len(created) == 1
    # The killer's transport is closed only when its wait() is awaited.
    assert created[0].waited is True


async def test_run_capture_returns_output() -> None:
    stdout, stderr, returncode = await run_capture(
        [sys.executable, "-c", "print('hello')"]
    )
    assert returncode == 0
    assert stdout.strip() == b"hello"
    assert stderr == b""


async def test_stream_command_times_out_hanging_process(tmp_path) -> None:
    result = await stream_command(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        timeout=1,
    )
    assert result.timed_out is True
    assert result.returncode is not None


async def test_stream_command_stops_when_a_daemon_inherits_stdout(tmp_path) -> None:
    pidfile = tmp_path / "child.pid"
    code = (
        "import subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(child.pid))\n"
        "print('parent done')\n"
    )
    started = time.monotonic()
    try:
        result = await stream_command(
            [sys.executable, "-c", code], cwd=tmp_path, timeout=10
        )
        elapsed = time.monotonic() - started
        assert result.output.strip() == "parent done"
        assert elapsed < 8
    finally:
        if pidfile.exists():
            pid = int(pidfile.read_text(encoding="utf-8"))
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    check=False,
                )
            else:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, 9)


async def test_stream_command_detaches_tool_processes_from_the_console(
    monkeypatch, tmp_path
) -> None:
    if os.name != "nt":
        pytest.skip("Windows console isolation")
    seen: list[dict[str, object]] = []

    async def fake_exec(*args: str, **kwargs: object) -> _FakeProcess:
        seen.append(kwargs)
        # stream_command only reaches the console-flag decision first; stop here.
        raise OSError("stop")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(OSError):
        await stream_command(["cmd", "/c", "echo"], cwd=tmp_path)
    assert seen[0]["creationflags"] == 0x08000000

    seen.clear()
    with pytest.raises(OSError):
        await stream_command(["cmd", "/c", "echo"], cwd=tmp_path, new_session=False)
    # Commands that need the controlling terminal keep it.
    assert seen[0]["creationflags"] == 0
