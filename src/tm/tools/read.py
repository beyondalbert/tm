from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from tm.compat import to_thread
from tm.tools.base import Tool, ToolContext, ToolResult, text_result
from tm.tools.path_utils import resolve_path
from tm.tools.truncate import format_size, truncate_head


class ReadParams(BaseModel):
    path: str
    offset: Optional[int] = None
    limit: Optional[int] = None


class ReadTool(Tool[ReadParams]):
    name = "read"
    description = (
        "Read a UTF-8 text file, with optional 1-based offset and line limit. "
        "Output is truncated to the first 2000 lines or 50KB; when truncated, "
        "continue with the offset shown in the result."
    )
    parameters_model = ReadParams
    replay_safe = True

    def __init__(self, *, dedupe: bool = False) -> None:
        #: Answer a repeated read of an unchanged file/range with a short note.
        self._dedupe = dedupe
        self._seen: dict[tuple[str | None, str, int, int], tuple[int, int]] = {}

    @property
    def dedupe(self) -> bool:
        return self._dedupe

    @dedupe.setter
    def dedupe(self, value: bool) -> None:
        self._dedupe = value

    async def execute(self, call_id: str, args: ReadParams, ctx: ToolContext) -> ToolResult:
        path = resolve_path(ctx.cwd, args.path)
        if not path.exists():
            return text_result(f"File not found: {path}", is_error=True)
        if path.is_dir():
            return text_result(f"Path is a directory: {path}", is_error=True)
        try:
            data = await to_thread(path.read_bytes)
            stat = path.stat()
        except OSError as exc:
            return text_result(f"Could not read {path}: {exc}", is_error=True)

        if b"\x00" in data[:8000]:
            return text_result(f"Binary file, not shown: {path}", is_error=True)

        offset = args.offset or 1
        limit = args.limit or 0
        key = (ctx.session, str(path), offset, limit)
        stamp = (stat.st_mtime_ns, stat.st_size)
        if self._dedupe and self._seen.get(key) == stamp:
            span = f"from line {offset}" + (f", limit {limit}" if limit else "")
            return text_result(
                f"[{path} ({span}) is unchanged since the previous read in this "
                "session; reuse that result (or its spill file if it was omitted).]"
            )

        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        start = max(offset - 1, 0)
        end = start + args.limit if args.limit else len(lines)
        window = lines[start:end]
        if not window and start > 0:
            return text_result(f"No lines at offset {start + 1} in {path}")

        if len(self._seen) >= 512:
            self._seen.pop(next(iter(self._seen)), None)
        self._seen[key] = stamp

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
