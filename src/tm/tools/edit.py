from __future__ import annotations

from pydantic import BaseModel

from tm.safety import record_file_change
from tm.tools.base import Tool, ToolContext, ToolResult, text_result
from tm.tools.path_utils import resolve_path


class EditOperation(BaseModel):
    old_text: str
    new_text: str


class EditParams(BaseModel):
    path: str
    edits: list[EditOperation]


class EditTool(Tool[EditParams]):
    name = "edit"
    description = (
        "Apply exact-text replacements to a file. Each old_text must occur "
        "exactly once so edits stay unambiguous."
    )
    parameters_model = EditParams
    execution_mode = "sequential"

    async def execute(self, call_id: str, args: EditParams, ctx: ToolContext) -> ToolResult:
        path = resolve_path(ctx.cwd, args.path)
        if not path.exists():
            return text_result(f"File not found: {path}", is_error=True)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            return text_result(f"Could not read {path}: {exc}", is_error=True)

        updated = text
        for index, operation in enumerate(args.edits, start=1):
            count = updated.count(operation.old_text)
            if count == 0:
                return text_result(
                    f"Edit {index}: old_text not found in {path}", is_error=True
                )
            if count > 1:
                return text_result(
                    f"Edit {index}: old_text matches {count} times in {path}; "
                    "include more context to make it unique",
                    is_error=True,
                )
            updated = updated.replace(operation.old_text, operation.new_text, 1)

        if ctx.dry_run:
            return text_result(
                f"[dry-run] would apply {len(args.edits)} edit(s) to {path}"
            )
        try:
            path.write_text(updated, encoding="utf-8")
        except OSError as exc:
            return text_result(f"Could not write {path}: {exc}", is_error=True)
        record_file_change(
            ctx.journal,
            session=ctx.session,
            tool="edit",
            path=path,
            existed=True,
            content=text.encode("utf-8"),
            summary=f"edit {path}",
        )
        return text_result(f"Applied {len(args.edits)} edit(s) to {path}")


__all__ = ["EditOperation", "EditParams", "EditTool"]
