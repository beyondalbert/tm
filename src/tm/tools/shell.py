from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path

from pydantic import BaseModel

from tm.tools.base import Tool, ToolContext, ToolResult, text_result
from tm.tools.truncate import truncate_text


def shell_argv(command: str) -> list[str]:
    """Return the argv to run ``command`` in the platform's native shell."""
    override = os.environ.get("TM_SHELL")
    if override:
        if os.name == "nt":
            return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
        return [override, "-lc", command]
    if os.name == "nt":
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["/bin/bash", "-lc", command]


async def _kill(proc: asyncio.subprocess.Process) -> None:
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


class ShellParams(BaseModel):
    command: str
    timeout: int | None = None
    cwd: str | None = None


class ShellTool(Tool[ShellParams]):
    name = "shell"
    description = "Run a shell command and return its combined stdout/stderr."
    parameters_model = ShellParams
    execution_mode = "sequential"

    async def execute(self, call_id: str, args: ShellParams, ctx: ToolContext) -> ToolResult:
        cwd = Path(args.cwd) if args.cwd else ctx.cwd
        if not cwd.is_absolute():
            cwd = ctx.cwd / cwd
        if not cwd.exists():
            return text_result(f"Working directory does not exist: {cwd}", is_error=True)

        argv = shell_argv(args.command)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=(os.name != "nt"),
            )
        except OSError as exc:
            return text_result(f"Could not start shell: {exc}", is_error=True)

        chunks: list[str] = []

        async def read_output() -> None:
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                chunks.append(text)
                if ctx.on_update is not None:
                    await ctx.on_update(text)

        reader = asyncio.create_task(read_output())

        timed_out = False
        aborted = False

        async def watchdog() -> None:
            nonlocal timed_out, aborted
            tasks: list[asyncio.Task] = []
            if ctx.signal is not None:
                tasks.append(asyncio.create_task(ctx.signal.wait()))
            if args.timeout:
                tasks.append(asyncio.create_task(asyncio.sleep(args.timeout)))
            if not tasks:
                return
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if ctx.signal is not None and ctx.signal.aborted:
                    aborted = True
                else:
                    timed_out = True
                await _kill(proc)
            finally:
                for task in tasks:
                    task.cancel()

        watch = asyncio.create_task(watchdog())
        await proc.wait()
        watch.cancel()
        await reader

        output, truncated = truncate_text("".join(chunks).rstrip())
        notes = []
        if aborted:
            notes.append("aborted")
        elif timed_out:
            notes.append(f"timed out after {args.timeout}s")
        notes.append(f"exit code {proc.returncode}")
        summary = output + ("\n" if output else "") + f"[{', '.join(notes)}]"
        if truncated:
            summary += "\n... (output truncated)"
        return text_result(summary, is_error=aborted or timed_out)


__all__ = ["ShellParams", "ShellTool", "shell_argv"]
