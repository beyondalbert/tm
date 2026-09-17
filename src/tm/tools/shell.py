from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel

from tm.tools.base import Tool, ToolContext, ToolResult, text_result
from tm.tools.subprocess_utils import stream_command
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
            result = await stream_command(
                argv,
                cwd=cwd,
                timeout=args.timeout,
                signal=ctx.signal,
                on_update=ctx.on_update,
            )
        except OSError as exc:
            return text_result(f"Could not start shell: {exc}", is_error=True)

        output, truncated = truncate_text(result.output)
        notes = []
        if result.aborted:
            notes.append("aborted")
        elif result.timed_out:
            notes.append(f"timed out after {args.timeout}s")
        notes.append(f"exit code {result.returncode}")
        summary = output + ("\n" if output else "") + f"[{', '.join(notes)}]"
        if truncated:
            summary += "\n... (output truncated)"
        return text_result(summary, is_error=result.aborted or result.timed_out)


__all__ = ["ShellParams", "ShellTool", "shell_argv"]
