from __future__ import annotations

from pydantic import BaseModel

from tm.tools.base import Tool, ToolContext, ToolResult, text_result
from tm.tools.path_utils import resolve_path


class LsParams(BaseModel):
    path: str = "."
    limit: int = 500


class LsTool(Tool[LsParams]):
    name = "ls"
    description = "List directory entries, marking directories with a trailing slash."
    parameters_model = LsParams

    async def execute(self, call_id: str, args: LsParams, ctx: ToolContext) -> ToolResult:
        path = resolve_path(ctx.cwd, args.path)
        if not path.exists():
            return text_result(f"Directory not found: {path}", is_error=True)
        if not path.is_dir():
            return text_result(f"Not a directory: {path}", is_error=True)
        try:
            entries = sorted(path.iterdir(), key=lambda entry: entry.name.lower())
        except OSError as exc:
            return text_result(f"Could not list {path}: {exc}", is_error=True)

        truncated = len(entries) > args.limit
        lines = [
            f"{entry.name}/" if entry.is_dir() else entry.name
            for entry in entries[: args.limit]
        ]
        body = "\n".join(lines) or "(empty directory)"
        if truncated:
            body += f"\n... ({len(entries) - args.limit} more entries)"
        return text_result(body)


__all__ = ["LsParams", "LsTool"]
