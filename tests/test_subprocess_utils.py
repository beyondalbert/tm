from __future__ import annotations

import asyncio
import os
import sys
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
