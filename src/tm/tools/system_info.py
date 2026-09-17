"""The ``system_info`` tool: report what the machine actually has."""

from __future__ import annotations

from pydantic import BaseModel

from tm.host import detect_host, render_environment
from tm.tools.base import Tool, ToolContext, ToolResult, text_result


class SystemInfoParams(BaseModel):
    full: bool = False


class SystemInfoTool(Tool[SystemInfoParams]):
    name = "system_info"
    description = (
        "Report the machine's OS, CPU, memory, disk, GPUs, Python interpreter, "
        "and installed tools. Inspect this before assuming what is available."
    )
    parameters_model = SystemInfoParams
    replay_safe = True

    async def execute(
        self, call_id: str, args: SystemInfoParams, ctx: ToolContext
    ) -> ToolResult:
        return text_result(render_environment(detect_host(), full=args.full))


__all__ = ["SystemInfoParams", "SystemInfoTool"]
