from __future__ import annotations

import asyncio
import fnmatch
import shutil
from pathlib import Path

from pydantic import BaseModel

from tm.tools.base import Tool, ToolContext, ToolResult, text_result
from tm.tools.path_utils import resolve_path
from tm.tools.truncate import truncate_text

_SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", ".mypy_cache"}


class FindParams(BaseModel):
    pattern: str
    path: str = "."
    max_results: int = 200


def _python_find(base: Path, args: FindParams) -> list[str]:
    iterator = [base] if base.is_file() else base.rglob("*")
    results: list[str] = []
    for candidate in iterator:
        if any(part in _SKIP_DIRS for part in candidate.parts):
            continue
        relative = candidate.relative_to(base) if base.is_dir() else candidate.name
        if fnmatch.fnmatch(candidate.name, args.pattern) or fnmatch.fnmatch(
            str(relative).replace("\\", "/"), args.pattern
        ):
            results.append(str(candidate))
            if len(results) >= args.max_results:
                break
    return results


class FindTool(Tool[FindParams]):
    name = "find"
    description = "Find files by glob pattern."
    parameters_model = FindParams
    replay_safe = True

    async def execute(self, call_id: str, args: FindParams, ctx: ToolContext) -> ToolResult:
        base = resolve_path(ctx.cwd, args.path)
        if not base.exists():
            return text_result(f"Path not found: {base}", is_error=True)

        fd = shutil.which("fd") or shutil.which("fdfind")
        if fd:
            argv = [
                fd,
                "--color=never",
                "--hidden",
                "--glob",
                "--max-results",
                str(args.max_results),
                "--",
                args.pattern,
                str(base),
            ]
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode not in (0, 1):
                return text_result(
                    f"find failed: {stderr.decode(errors='replace').strip()}", is_error=True
                )
            results = stdout.decode(errors="replace").splitlines()
        else:
            results = _python_find(base, args)

        if not results:
            return text_result(f"No files matching '{args.pattern}'")
        body, truncated = truncate_text("\n".join(results))
        if truncated:
            body += "\n... (output truncated)"
        return text_result(body)


__all__ = ["FindParams", "FindTool"]
