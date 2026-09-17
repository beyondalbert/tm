from __future__ import annotations

import asyncio
import fnmatch
import re
import shutil
from pathlib import Path

from pydantic import BaseModel

from tm.tools.base import Tool, ToolContext, ToolResult, text_result
from tm.tools.output import format_truncation
from tm.tools.path_utils import resolve_path
from tm.tools.truncate import GREP_MAX_LINE_LENGTH, truncate_head, truncate_line

_SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", ".mypy_cache"}


class GrepParams(BaseModel):
    pattern: str
    path: str = "."
    glob: str | None = None
    ignore_case: bool = False
    fixed_strings: bool = False
    max_results: int = 200


def _python_search(base: Path, args: GrepParams) -> tuple[list[str], int]:
    flags = re.IGNORECASE if args.ignore_case else 0
    regex = None if args.fixed_strings else re.compile(args.pattern, flags)
    needle = args.pattern.lower() if args.ignore_case else args.pattern

    files = [base] if base.is_file() else base.rglob("*")
    matches: list[str] = []
    total = 0
    for candidate in files:
        if not candidate.is_file():
            continue
        if any(part in _SKIP_DIRS for part in candidate.parts):
            continue
        if args.glob and not fnmatch.fnmatch(candidate.name, args.glob):
            continue
        try:
            raw = candidate.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:8000]:
            continue
        text = raw.decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            hit = (
                (needle in (line.lower() if args.ignore_case else line))
                if args.fixed_strings
                else bool(regex and regex.search(line))
            )
            if hit:
                total += 1
                if len(matches) < args.max_results:
                    matches.append(f"{candidate}:{number}:{line}")
    return matches, total


class GrepTool(Tool[GrepParams]):
    name = "grep"
    description = (
        "Search file contents by regular expression or literal string. Results are "
        f"capped at max_results and each match line at {GREP_MAX_LINE_LENGTH} chars; "
        "output is truncated to the first 2000 lines or 50KB, so refine the pattern "
        "if results look cut off."
    )
    parameters_model = GrepParams
    replay_safe = True

    async def execute(self, call_id: str, args: GrepParams, ctx: ToolContext) -> ToolResult:
        base = resolve_path(ctx.cwd, args.path)
        if not base.exists():
            return text_result(f"Path not found: {base}", is_error=True)

        rg = shutil.which("rg")
        if rg:
            argv = [rg, "--line-number", "--no-heading", "--color=never", "--hidden"]
            if args.ignore_case:
                argv.append("-i")
            if args.fixed_strings:
                argv.append("-F")
            if args.glob:
                argv.extend(["--glob", args.glob])
            argv.extend(["--", args.pattern, str(base)])
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode not in (0, 1):
                return text_result(
                    f"grep failed: {stderr.decode(errors='replace').strip()}", is_error=True
                )
            lines = stdout.decode(errors="replace").splitlines()
            total = len(lines)
            shown = lines[: args.max_results]
        else:
            shown, total = _python_search(base, args)

        if not shown:
            return text_result(f"No matches for '{args.pattern}'")
        shown = [truncate_line(line)[0] for line in shown]
        truncation = truncate_head("\n".join(shown))
        body = truncation.content
        if truncation.truncated:
            body += ("\n" if body else "") + format_truncation(truncation, tail=False)
            body += f"\n... ({total} matches, showing {truncation.output_lines})"
        elif total > len(shown):
            body += f"\n... ({total} matches, showing {len(shown)})"
        return text_result(body)


__all__ = ["GrepParams", "GrepTool"]
