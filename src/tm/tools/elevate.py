"""Run a shell command with administrator/root privileges (guided elevation)."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from tm.host import detect_host
from tm.tools.base import Tool, ToolContext, ToolResult, text_result
from tm.tools.output import format_truncation, spill_output
from tm.tools.shell import shell_argv
from tm.tools.subprocess_utils import stream_command
from tm.tools.truncate import truncate_tail


class ElevateParams(BaseModel):
    command: str
    cwd: str | None = None
    timeout: int | None = None


class ElevateTool(Tool[ElevateParams]):
    name = "elevate"
    description = (
        "Run a shell command with administrator/root privileges. On Windows this "
        "triggers a UAC prompt; on Linux it uses sudo/pkexec. Use it only when a "
        "normal command failed for lack of privileges. Output is truncated to the "
        "last 2000 lines or 50KB; a non-zero exit code is reported as an error."
    )
    parameters_model = ElevateParams
    execution_mode = "sequential"
    replay_safe = False

    async def execute(
        self, call_id: str, args: ElevateParams, ctx: ToolContext
    ) -> ToolResult:
        host = detect_host()
        argv = shell_argv(args.command)
        already_elevated = host.identity().elevated
        if not already_elevated:
            argv = host.elevated_argv(argv)

        cwd = Path(args.cwd) if args.cwd else ctx.cwd
        if not cwd.is_absolute():
            cwd = ctx.cwd / cwd
        if not cwd.exists():
            return text_result(f"Working directory does not exist: {cwd}", is_error=True)
        if ctx.dry_run:
            return text_result(f"[dry-run] would run elevated: {args.command}")

        try:
            result = await stream_command(
                argv,
                cwd=cwd,
                timeout=args.timeout,
                signal=ctx.signal,
                on_update=ctx.on_update,
                new_session=False,
            )
        except OSError as exc:
            return text_result(f"Could not start elevated command: {exc}", is_error=True)

        truncation = truncate_tail(result.output)
        full_path = (
            spill_output(result.output, name="elevate") if truncation.truncated else None
        )
        notes = []
        if result.aborted:
            notes.append("aborted")
        elif result.timed_out:
            notes.append(f"timed out after {args.timeout}s")
        notes.append(f"exit code {result.returncode}")
        if already_elevated:
            notes.append("already elevated")
        parts = [truncation.content] if truncation.content else []
        notice = format_truncation(truncation, full_path=full_path, tail=True)
        if notice:
            parts.append(notice)
        parts.append(f"[{', '.join(notes)}]")
        return text_result(
            "\n".join(parts),
            is_error=result.aborted or result.timed_out or result.returncode != 0,
        )


__all__ = ["ElevateParams", "ElevateTool"]
