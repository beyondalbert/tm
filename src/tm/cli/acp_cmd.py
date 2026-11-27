"""``tm acp``: serve TM over the Agent Client Protocol (stdio, ACP subset)."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Optional

import typer

from tm.cli.serve_cmd import EmbeddingOptions, build_host, err
from tm.rpc.acp import AcpServer
from tm.rpc.server import RpcServer

app = typer.Typer(
    add_completion=False,
    help="Serve TM over the Agent Client Protocol (stdio).",
)


@app.command()
def acp(
    mode: str = typer.Option(
        "host",
        "--mode",
        help="Approval mode: host (ask the editor), policy (deny asks), yolo (allow).",
    ),
    cwd: Optional[str] = typer.Option(None, "--cwd", help="Working directory."),
    provider: Optional[str] = typer.Option(None, "--provider", help="Provider id."),
    model: Optional[str] = typer.Option(None, "--model", help="Model id or pattern."),
    read_only: bool = typer.Option(False, "--read-only", help="Only read-only tools."),
    no_session: bool = typer.Option(False, "--no-session", help="Do not persist sessions."),
    approve: bool = typer.Option(
        False, "--approve", "-a", help="Trust project-local files for this run."
    ),
    no_approve: bool = typer.Option(
        False, "--no-approve", "-na", help="Ignore project-local files for this run."
    ),
    approval_timeout: int = typer.Option(
        600000, "--approval-timeout", help="Milliseconds to wait for an editor answer."
    ),
    max_sessions: int = typer.Option(
        2, "--max-sessions", help="How many sessions this process may host."
    ),
) -> None:
    options = EmbeddingOptions(
        mode=mode,
        cwd=cwd,
        provider=provider,
        model=model,
        read_only=read_only,
        no_session=no_session,
        approve=approve,
        no_approve=no_approve,
        approval_timeout=approval_timeout,
        max_sessions=max_sessions,
        approval_style="acp",
    )
    host, registry = build_host(options)
    err.print(
        f"tm acp: mode={options.mode} "
        f"cwd={Path(options.cwd).expanduser().resolve() if options.cwd else Path.cwd()}",
        markup=False,
        highlight=False,
    )

    async def run() -> None:
        server = RpcServer(AcpServer(host))
        try:
            await server.run()
        finally:
            await registry.aclose()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


__all__ = ["app"]
