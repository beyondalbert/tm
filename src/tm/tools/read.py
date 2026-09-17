from __future__ import annotations

from pydantic import BaseModel

from tm.tools.base import Tool, ToolContext, ToolResult, text_result
from tm.tools.path_utils import resolve_path
from tm.tools.truncate import format_size, truncate_head


class ReadParams(BaseModel):
    path: str
    offset: int | None = None
    limit: int | None = None


class ReadTool(Tool[ReadParams]):
    name = "read"
    description = (
        "Read a UTF-8 text file, with optional 1-based offset and line limit. "
        "Output is truncated to the first 2000 lines or 50KB; when truncated, "
        "continue with the offset shown in the result."
    )
    parameters_model = ReadParams
    replay_safe = True

    async def execute(self, call_id: str, args: ReadParams, ctx: ToolContext) -> ToolResult:
        path = resolve_path(ctx.cwd, args.path)
        if not path.exists():
            return text_result(f"File not found: {path}", is_error=True)
        if path.is_dir():
            return text_result(f"Path is a directory: {path}", is_error=True)
        try:
            data = path.read_bytes()
        except OSError as exc:
            return text_result(f"Could not read {path}: {exc}", is_error=True)

        if b"\x00" in data[:8000]:
            return text_result(f"Binary file, not shown: {path}", is_error=True)

        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        start = max((args.offset or 1) - 1, 0)
        end = start + args.limit if args.limit else len(lines)
        window = lines[start:end]
        if not window and start > 0:
            return text_result(f"No lines at offset {start + 1} in {path}")

        truncation = truncate_head("\n".join(window))
        kept = truncation.content.split("\n") if truncation.content else []
        numbered = "\n".join(
            f"{start + i + 1:6d}: {line}" for i, line in enumerate(kept)
        )
        if truncation.truncated:
            first = start + 1
            if truncation.output_lines == 0:
                hint = (
                    f"[Line {first} exceeds {format_size(truncation.max_bytes)}; "
                    f"use offset={first} with a smaller limit.]"
                )
            else:
                last = start + truncation.output_lines
                hint = (
                    f"[Showing lines {first}-{last} of {len(lines)}. "
                    f"Use offset={last + 1} to continue.]"
                )
            numbered = f"{numbered}\n\n{hint}" if numbered else hint
        return text_result(numbered or "(empty file)")


__all__ = ["ReadParams", "ReadTool"]
