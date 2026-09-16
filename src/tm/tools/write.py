from __future__ import annotations

from pydantic import BaseModel

from tm.tools.base import Tool, ToolContext, ToolResult, text_result
from tm.tools.path_utils import resolve_path


class WriteParams(BaseModel):
    path: str
    content: str


class WriteTool(Tool[WriteParams]):
    name = "write"
    description = "Create or overwrite a file, creating parent directories as needed."
    parameters_model = WriteParams

    async def execute(self, call_id: str, args: WriteParams, ctx: ToolContext) -> ToolResult:
        path = resolve_path(ctx.cwd, args.path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args.content, encoding="utf-8")
        except OSError as exc:
            return text_result(f"Could not write {path}: {exc}", is_error=True)
        line_count = args.content.count("\n") + (1 if args.content else 0)
        return text_result(f"Wrote {line_count} lines to {path}")


__all__ = ["WriteParams", "WriteTool"]
