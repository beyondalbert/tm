"""Background process management: detached start, status, logs, stop, restart."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from tm.config import config_dir
from tm.host import Host, detect_host
from tm.jobs import Job, JobStore, new_job_id
from tm.safety import new_change
from tm.tools.base import Tool, ToolContext, ToolResult, text_result
from tm.tools.shell import shell_argv


def _tail(path: Path, lines: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


class ProcessParams(BaseModel):
    action: Literal["start", "status", "logs", "stop", "restart", "list"] = "list"
    command: str | None = None
    description: str | None = None
    cwd: str | None = None
    job: str | None = None
    lines: int = 50


class ProcessTool(Tool[ProcessParams]):
    name = "process"
    description = (
        "Manage long-running background processes: start a detached command with "
        "logging, then check status, read logs, stop, or restart it. Use this for "
        "servers and daemons that must outlive a single command."
    )
    parameters_model = ProcessParams
    execution_mode = "sequential"
    replay_safe = False

    def __init__(self, *, host: Host | None = None, jobs_root: Path | None = None) -> None:
        self._host = host
        self._jobs_root = jobs_root

    def _host_instance(self) -> Host:
        return self._host or detect_host()

    def _store(self) -> JobStore:
        return JobStore(self._jobs_root or (config_dir() / "jobs"))

    async def execute(
        self, call_id: str, args: ProcessParams, ctx: ToolContext
    ) -> ToolResult:
        if args.action == "start":
            return await self._start(args, ctx)
        if args.action == "list":
            return await self._list(ctx)
        if args.action == "status":
            return await self._status(args, ctx)
        if args.action == "logs":
            return await self._logs(args)
        if args.action == "stop":
            return await self._stop(args, ctx)
        return await self._restart(args, ctx)

    def _cwd(self, args: ProcessParams, ctx: ToolContext) -> Path:
        cwd = Path(args.cwd) if args.cwd else ctx.cwd
        if not cwd.is_absolute():
            cwd = ctx.cwd / cwd
        return cwd

    async def _start(self, args: ProcessParams, ctx: ToolContext) -> ToolResult:
        if not args.command:
            return text_result("start requires a command", is_error=True)
        cwd = self._cwd(args, ctx)
        if not cwd.exists():
            return text_result(f"Working directory does not exist: {cwd}", is_error=True)
        if ctx.dry_run:
            return text_result(f"[dry-run] would start `{args.command}` in {cwd}")
        return await self._spawn(args.command, args.description, cwd, None, ctx)

    async def _spawn(
        self,
        command: str,
        description: str | None,
        cwd: Path,
        existing: Job | None,
        ctx: ToolContext,
    ) -> ToolResult:
        host = self._host_instance()
        store = self._store()
        job_id = existing.id if existing else new_job_id()
        log = store.root / f"{job_id}.log"
        try:
            pid = await asyncio.to_thread(
                host.spawn_detached, shell_argv(command), cwd=cwd, log_path=log
            )
        except (OSError, NotImplementedError) as exc:
            return text_result(f"Could not start process: {exc}", is_error=True)
        job = Job(
            id=job_id,
            description=description or command,
            command=command,
            cwd=str(cwd),
            pid=pid,
            log=str(log),
            started=time.time(),
            status="running",
        )
        store.save(job)
        self._journal(ctx, job, "restart" if existing else "start")
        return text_result(
            f"Started job {job.id} (pid {pid})\ncommand: {command}\nlog: {log}"
        )

    async def _list(self, ctx: ToolContext) -> ToolResult:
        jobs = self._store().list()
        if not jobs:
            return text_result("No jobs.")
        host = self._host_instance()
        lines = []
        for job in jobs:
            alive = (
                await asyncio.to_thread(host.process_alive, job.pid)
                if job.status == "running"
                else False
            )
            status = "exited" if job.status == "running" and not alive else job.status
            lines.append(f"{job.id} [{status}] pid {job.pid} - {job.description}")
        return text_result("\n".join(lines))

    async def _status(self, args: ProcessParams, ctx: ToolContext) -> ToolResult:
        store = self._store()
        job = store.resolve(args.job)
        if job is None:
            return text_result("No matching job. Use action 'list'.", is_error=True)
        host = self._host_instance()
        alive = await asyncio.to_thread(host.process_alive, job.pid)
        if job.status == "running" and not alive:
            job.status = "exited"
            store.save(job)
        lines = [f"Job {job.id} [{job.status}] pid {job.pid}", f"command: {job.command}"]
        tail = _tail(Path(job.log), 10)
        if tail:
            lines.append("recent output:\n" + tail)
        return text_result("\n".join(lines))

    async def _logs(self, args: ProcessParams) -> ToolResult:
        job = self._store().resolve(args.job)
        if job is None:
            return text_result("No matching job. Use action 'list'.", is_error=True)
        text = _tail(Path(job.log), max(1, args.lines))
        return text_result(text or "(no output yet)")

    async def _stop(self, args: ProcessParams, ctx: ToolContext) -> ToolResult:
        store = self._store()
        job = store.resolve(args.job)
        if job is None:
            return text_result("No matching job. Use action 'list'.", is_error=True)
        if ctx.dry_run:
            return text_result(f"[dry-run] would stop job {job.id} (pid {job.pid})")
        await asyncio.to_thread(self._host_instance().kill_tree, job.pid)
        job.status = "stopped"
        store.save(job)
        self._journal(ctx, job, "stop")
        return text_result(f"Stopped job {job.id} (pid {job.pid})")

    async def _restart(self, args: ProcessParams, ctx: ToolContext) -> ToolResult:
        store = self._store()
        job = store.resolve(args.job)
        if job is None:
            return text_result("No matching job. Use action 'list'.", is_error=True)
        if ctx.dry_run:
            return text_result(f"[dry-run] would restart job {job.id}")
        await asyncio.to_thread(self._host_instance().kill_tree, job.pid)
        return await self._spawn(
            job.command, job.description, Path(job.cwd), job, ctx
        )

    def _journal(self, ctx: ToolContext, job: Job, action: str) -> None:
        if ctx.journal is None:
            return
        ctx.journal.record(
            new_change(
                tool="process",
                kind="process",
                target=job.id,
                summary=f"{action} job {job.id}: {job.command}",
                reversible=False,
                session=ctx.session,
                undo={"job": job.id, "action": action},
            )
        )


__all__ = ["ProcessParams", "ProcessTool"]
