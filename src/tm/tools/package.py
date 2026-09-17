"""System package query/install/uninstall via the platform package manager."""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel

from tm.host import Host, detect_host
from tm.safety import new_change
from tm.tools.base import Tool, ToolContext, ToolResult, text_result


class PackageParams(BaseModel):
    action: Literal["query", "install", "uninstall"]
    name: str


class PackageTool(Tool[PackageParams]):
    name = "package"
    description = (
        "Query, install, or uninstall a system package with the platform package "
        "manager (winget, apt, or dnf). Installing usually needs administrator "
        "rights; if it fails, retry the command with the elevate tool."
    )
    parameters_model = PackageParams
    execution_mode = "sequential"
    replay_safe = False

    def __init__(self, *, host: Host | None = None) -> None:
        self._host = host

    def _host_instance(self) -> Host:
        return self._host or detect_host()

    async def execute(
        self, call_id: str, args: PackageParams, ctx: ToolContext
    ) -> ToolResult:
        host = self._host_instance()
        if args.action == "query":
            installed, output = await asyncio.to_thread(host.package_query, args.name)
            state = "installed" if installed else "not installed"
            return text_result(f"{args.name}: {state}\n{output}")

        if ctx.dry_run:
            return text_result(f"[dry-run] would {args.action} package {args.name}")

        was_installed, _ = await asyncio.to_thread(host.package_query, args.name)
        ok, output = await asyncio.to_thread(
            host.package_action, args.action, [args.name]
        )
        if ctx.journal is not None:
            ctx.journal.record(
                new_change(
                    tool="package",
                    kind="package",
                    target=args.name,
                    summary=f"{args.action} package {args.name}",
                    reversible=True,
                    session=ctx.session,
                    undo={
                        "name": args.name,
                        "action": args.action,
                        "was_installed": was_installed,
                    },
                )
            )
        result = "ok" if ok else "failed"
        return text_result(
            f"{args.action} {args.name}: {result}\n{output}", is_error=not ok
        )


__all__ = ["PackageParams", "PackageTool"]
