"""Shared subprocess execution used by the shell and Python tools."""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from tm.tools.base import OnUpdate
from tm.utils.abort import AbortSignal


@dataclass
class CommandResult:
    output: str
    returncode: int | None
    timed_out: bool
    aborted: bool


async def kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    if os.name == "nt":
        await asyncio.create_subprocess_exec(
            "taskkill", "/pid", str(proc.pid), "/T", "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    else:
        killpg = getattr(os, "killpg", None)
        getpgid = getattr(os, "getpgid", None)
        if killpg is not None and getpgid is not None:
            try:
                killpg(getpgid(proc.pid), 9)
            except (ProcessLookupError, PermissionError):
                proc.kill()
        else:
            proc.kill()
    with contextlib.suppress(ProcessLookupError):
        await proc.wait()


async def stream_command(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int | None = None,
    signal: AbortSignal | None = None,
    on_update: OnUpdate | None = None,
    env: Mapping[str, str] | None = None,
    new_session: bool = True,
) -> CommandResult:
    """Run ``argv`` to completion, streaming combined output.

    Set ``new_session=False`` for commands that need the controlling terminal
    (for example ``sudo``, which reads the password from ``/dev/tty``).

    Raises :class:`OSError` when the process cannot be started.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=new_session and os.name != "nt",
        env=env,
    )

    chunks: list[str] = []

    async def read_output() -> None:
        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            chunks.append(text)
            if on_update is not None:
                await on_update(text)

    reader = asyncio.create_task(read_output())
    timed_out = False
    aborted = False

    async def watchdog() -> None:
        nonlocal timed_out, aborted
        tasks: list[asyncio.Task] = []
        if signal is not None:
            tasks.append(asyncio.create_task(signal.wait()))
        if timeout:
            tasks.append(asyncio.create_task(asyncio.sleep(timeout)))
        if not tasks:
            return
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if signal is not None and signal.aborted:
                aborted = True
            else:
                timed_out = True
            await kill_process_tree(proc)
        finally:
            for task in tasks:
                task.cancel()

    watch = asyncio.create_task(watchdog())
    await proc.wait()
    watch.cancel()
    await reader

    return CommandResult(
        output="".join(chunks).rstrip(),
        returncode=proc.returncode,
        timed_out=timed_out,
        aborted=aborted,
    )


__all__ = ["CommandResult", "kill_process_tree", "stream_command"]
