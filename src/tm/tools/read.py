from __future__ import annotations

from pydantic import BaseModel

from tm.tools.base import Tool, ToolContext, ToolResult, text_result
from tm.tools.path_utils import resolve_path
from tm.tools.truncate import truncate_text


class ReadParams(BaseModel):
    path: str
    offset: int | None = None
    limit: int | None = None


class ReadTool(Tool[ReadParams]):
    name = "read"
    description = "Read a UTF-8 text file, with optional 1-based offset and line limit."
    parameters_model = ReadParams

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

        numbered = "\n".join(f"{start + i + 1:6d}: {line}" for i, line in enumerate(window))
        numbered, truncated = truncate_text(numbered)
        if truncated:
            numbered += "\n... (output truncated)"
        return text_result(numbered or "(empty file)")


__all__ = ["ReadParams", "ReadTool"]
