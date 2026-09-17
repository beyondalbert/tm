from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from tm.host.base import Host, Identity, Resources, Software, SystemInfo
from tm.jobs import JobStore
from tm.safety import Journal
from tm.tools.base import ToolContext
from tm.tools.process import ProcessParams, ProcessTool


class FakeHost(Host):
    name = "fake"

    def __init__(self) -> None:
        self.spawned: list[int] = []
        self.alive: dict[int, bool] = {}
        self.killed: list[int] = []

    def identity(self) -> Identity:
        return Identity(user="tester", elevated=False)

    def system(self) -> SystemInfo:
        return SystemInfo(os="linux", version="6", arch="x86_64", hostname="fake")

    def resources(self) -> Resources:
        return Resources(cpu_count=1, memory_total=0, memory_available=0)

    def software(self) -> Software:
        return Software()

    def spawn_detached(
        self,
        argv: list[str],
        *,
        cwd: Path,
        log_path: Path,
        env: Mapping[str, str] | None = None,
    ) -> int:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("server started\n", encoding="utf-8")
        pid = 1000 + len(self.spawned)
        self.spawned.append(pid)
        self.alive[pid] = True
        return pid

    def process_alive(self, pid: int) -> bool:
        return self.alive.get(pid, False)

    def kill_tree(self, pid: int) -> None:
        self.killed.append(pid)
        self.alive[pid] = False


def make_ctx(tmp_path: Path, journal: Journal | None = None, dry_run: bool = False):
    return ToolContext(cwd=tmp_path, journal=journal, session="s1", dry_run=dry_run)


async def start(tool: ProcessTool, tmp_path: Path, **kwargs):
    params = ProcessParams(action="start", command="serve", description="web")
    return await tool.execute("1", params.model_copy(update=kwargs), make_ctx(tmp_path))


async def test_process_start_status_logs_stop(tmp_path: Path) -> None:
    host = FakeHost()
    journal = Journal(tmp_path / "changes.jsonl")
    tool = ProcessTool(host=host, jobs_root=tmp_path / "jobs")
    ctx = make_ctx(tmp_path, journal)

    started = await tool.execute(
        "1", ProcessParams(action="start", command="serve", description="web"), ctx
    )
    assert "Started job" in started.text()
    job = JobStore(tmp_path / "jobs").list()[0]

    status = await tool.execute("2", ProcessParams(action="status"), ctx)
    assert "running" in status.text()

    logs = await tool.execute("3", ProcessParams(action="logs", lines=5), ctx)
    assert "server started" in logs.text()

    stopped = await tool.execute("4", ProcessParams(action="stop"), ctx)
    assert "Stopped job" in stopped.text()
    assert host.killed == [job.pid]

    entries = journal.entries(session="s1")
    assert [entry.kind for entry in entries] == ["process", "process"]
    assert all(entry.reversible is False for entry in entries)


async def test_process_status_reports_exited(tmp_path: Path) -> None:
    host = FakeHost()
    tool = ProcessTool(host=host, jobs_root=tmp_path / "jobs")
    ctx = make_ctx(tmp_path)
    await tool.execute("1", ProcessParams(action="start", command="serve"), ctx)
    job = JobStore(tmp_path / "jobs").list()[0]
    host.alive[job.pid] = False

    status = await tool.execute("2", ProcessParams(action="status"), ctx)
    assert "exited" in status.text()


async def test_process_dry_run_does_not_start(tmp_path: Path) -> None:
    host = FakeHost()
    tool = ProcessTool(host=host, jobs_root=tmp_path / "jobs")
    result = await tool.execute(
        "1",
        ProcessParams(action="start", command="serve"),
        make_ctx(tmp_path, dry_run=True),
    )
    assert "[dry-run]" in result.text()
    assert host.spawned == []
    assert JobStore(tmp_path / "jobs").list() == []


async def test_process_restart_uses_a_new_pid(tmp_path: Path) -> None:
    host = FakeHost()
    tool = ProcessTool(host=host, jobs_root=tmp_path / "jobs")
    ctx = make_ctx(tmp_path)
    await tool.execute("1", ProcessParams(action="start", command="serve"), ctx)
    first = JobStore(tmp_path / "jobs").list()[0]

    await tool.execute("2", ProcessParams(action="restart"), ctx)
    again = JobStore(tmp_path / "jobs").get(first.id)
    assert again is not None
    assert again.pid != first.pid
    assert again.status == "running"


async def test_process_list(tmp_path: Path) -> None:
    host = FakeHost()
    tool = ProcessTool(host=host, jobs_root=tmp_path / "jobs")
    ctx = make_ctx(tmp_path)
    empty = await tool.execute("1", ProcessParams(action="list"), ctx)
    assert empty.text() == "No jobs."
    await tool.execute("2", ProcessParams(action="start", command="serve"), ctx)
    listed = await tool.execute("3", ProcessParams(action="list"), ctx)
    assert "serve" in listed.text()
