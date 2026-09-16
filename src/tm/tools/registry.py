from __future__ import annotations

from tm.tools.base import Tool
from tm.tools.edit import EditTool
from tm.tools.find import FindTool
from tm.tools.grep import GrepTool
from tm.tools.ls import LsTool
from tm.tools.read import ReadTool
from tm.tools.shell import ShellTool
from tm.tools.write import WriteTool

DEFAULT_TOOL_NAMES = ("read", "write", "edit", "shell", "grep", "find", "ls")


def build_default_tools() -> list[Tool]:
    return [
        ReadTool(),
        WriteTool(),
        EditTool(),
        ShellTool(),
        GrepTool(),
        FindTool(),
        LsTool(),
    ]


def tools_by_name() -> dict[str, Tool]:
    return {tool.name: tool for tool in build_default_tools()}


__all__ = ["DEFAULT_TOOL_NAMES", "build_default_tools", "tools_by_name"]
