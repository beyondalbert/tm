"""OS service control (systemd unit or Windows service)."""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel

from tm.host import Host, detect_host
from tm.safety import new_change
from tm.tools.base import Tool, ToolContext, ToolResult, text_result


class ServiceParams(BaseModel):
    action: Literal["status", "start", "stop", "restart"]
    name: str


class ServiceTool(Tool[ServiceParams]):
    name = "service"
    description = (
        "Query or control an OS service (a systemd unit or a Windows service). "
        "Starting or stopping usually needs administrator rights; if it fails, "
        "retry the command with the elevate tool."
    )
    parameters_model = ServiceParams
    execution_mode = "sequential"
    replay_safe = False

    def __init__(self, *, host: Host | None = None) -> None:
        self._host = host

    def _host_instance(self) -> Host:
        return self._host or detect_host()

    async def execute(
        self, call_id: str, args: ServiceParams, ctx: ToolContext
    ) -> ToolResult:
        host = self._host_instance()
        if args.action == "status":
            active, output = await asyncio.to_thread(host.service_status, args.name)
            state = "active" if active else "inactive"
            return text_result(f"{args.name}: {state}\n{output}")

        if ctx.dry_run:
            return text_result(f"[dry-run] would {args.action} service {args.name}")

        was_active, _ = await asyncio.to_thread(host.service_status, args.name)
        ok, output = await asyncio.to_thread(host.service_action, args.name, args.action)
        if ctx.journal is not None:
            ctx.journal.record(
                new_change(
                    tool="service",
                    kind="service",
                    target=args.name,
                    summary=f"{args.action} service {args.name}",
                    reversible=args.action in ("start", "stop"),
                    session=ctx.session,
                    undo={
                        "name": args.name,
                        "action": args.action,
                        "was_active": was_active,
                    },
                )
            )
        result = "ok" if ok else "failed"
        return text_result(f"{args.action} {args.name}: {result}\n{output}", is_error=not ok)


__all__ = ["ServiceParams", "ServiceTool"]
