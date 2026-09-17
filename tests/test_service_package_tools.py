from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from tm.host.base import Host, Identity, Resources, Software, SystemInfo
from tm.safety import Journal, apply_undo
from tm.tools.base import ToolContext
from tm.tools.package import PackageParams, PackageTool
from tm.tools.service import ServiceParams, ServiceTool


class FakeHost(Host):
    name = "fake"

    def __init__(self) -> None:
        self.active: dict[str, bool] = {"nginx": True}
        self.installed: dict[str, bool] = {"jq": False}
        self.service_calls: list[tuple[str, str]] = []
        self.package_calls: list[tuple[str, list[str]]] = []

    def identity(self) -> Identity:
        return Identity(user="tester", elevated=False)

    def system(self) -> SystemInfo:
        return SystemInfo(os="linux", version="6", arch="x86_64", hostname="fake")

    def resources(self) -> Resources:
        return Resources(cpu_count=1, memory_total=0, memory_available=0)

    def software(self) -> Software:
        return Software()

    def service_status(self, name: str) -> tuple[bool, str]:
        return self.active.get(name, False), f"{name} state"

    def service_action(self, name: str, action: str) -> tuple[bool, str]:
        self.service_calls.append((name, action))
        if action == "start":
            self.active[name] = True
        elif action == "stop":
            self.active[name] = False
        return True, "ok"

    def package_query(self, name: str) -> tuple[bool, str]:
        return self.installed.get(name, False), "query output"

    def package_action(self, action: str, names: list[str]) -> tuple[bool, str]:
        self.package_calls.append((action, names))
        self.installed[names[0]] = action == "install"
        return True, "ok"

    def spawn_detached(
        self,
        argv: list[str],
        *,
        cwd: Path,
        log_path: Path,
        env: Mapping[str, str] | None = None,
    ) -> int:
        return 1


def ctx(tmp_path: Path, journal: Journal | None = None, dry_run: bool = False):
    return ToolContext(cwd=tmp_path, journal=journal, session="s1", dry_run=dry_run)


async def test_service_status(tmp_path: Path) -> None:
    tool = ServiceTool(host=FakeHost())
    result = await tool.execute("1", ServiceParams(action="status", name="nginx"), ctx(tmp_path))
    assert "active" in result.text()


async def test_service_start_is_reversible(tmp_path: Path) -> None:
    host = FakeHost()
    host.active["nginx"] = False
    journal = Journal(tmp_path / "changes.jsonl")
    tool = ServiceTool(host=host)

    await tool.execute("1", ServiceParams(action="start", name="nginx"), ctx(tmp_path, journal))
    change = journal.entries(session="s1")[0]
    assert change.kind == "service"
    assert change.reversible is True

    message = apply_undo(change, host)
    assert "stop nginx" in message
    assert host.active["nginx"] is False


async def test_service_dry_run_does_not_act(tmp_path: Path) -> None:
    host = FakeHost()
    tool = ServiceTool(host=host)
    result = await tool.execute(
        "1", ServiceParams(action="stop", name="nginx"), ctx(tmp_path, dry_run=True)
    )
    assert "[dry-run]" in result.text()
    assert host.service_calls == []


async def test_package_query_and_install_undo(tmp_path: Path) -> None:
    host = FakeHost()
    journal = Journal(tmp_path / "changes.jsonl")
    tool = PackageTool(host=host)

    query = await tool.execute("1", PackageParams(action="query", name="jq"), ctx(tmp_path))
    assert "not installed" in query.text()

    await tool.execute("2", PackageParams(action="install", name="jq"), ctx(tmp_path, journal))
    assert host.installed["jq"] is True
    change = journal.entries(session="s1")[0]
    assert change.kind == "package" and change.reversible

    message = apply_undo(change, host)
    assert "uninstalled jq" in message
    assert host.installed["jq"] is False


async def test_package_dry_run_does_not_act(tmp_path: Path) -> None:
    host = FakeHost()
    tool = PackageTool(host=host)
    result = await tool.execute(
        "1", PackageParams(action="install", name="jq"), ctx(tmp_path, dry_run=True)
    )
    assert "[dry-run]" in result.text()
    assert host.package_calls == []
