from __future__ import annotations

from tm.tools.base import Tool
from tm.tools.edit import EditTool
from tm.tools.elevate import ElevateTool
from tm.tools.find import FindTool
from tm.tools.grep import GrepTool
from tm.tools.ls import LsTool
from tm.tools.package import PackageTool
from tm.tools.process import ProcessTool
from tm.tools.python import PythonTool
from tm.tools.read import ReadTool
from tm.tools.service import ServiceTool
from tm.tools.shell import ShellTool
from tm.tools.system_info import SystemInfoTool
from tm.tools.write import WriteTool

DEFAULT_TOOL_NAMES = (
    "read",
    "write",
    "edit",
    "shell",
    "python",
    "grep",
    "find",
    "ls",
    "system_info",
    "process",
    "service",
    "package",
    "elevate",
)


def build_default_tools() -> list[Tool]:
    return [
        ReadTool(),
        WriteTool(),
        EditTool(),
        ShellTool(),
        PythonTool(),
        GrepTool(),
        FindTool(),
        LsTool(),
        SystemInfoTool(),
        ProcessTool(),
        ServiceTool(),
        PackageTool(),
        ElevateTool(),
    ]


def tools_by_name() -> dict[str, Tool]:
    return {tool.name: tool for tool in build_default_tools()}


__all__ = ["DEFAULT_TOOL_NAMES", "build_default_tools", "tools_by_name"]
