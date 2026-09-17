"""Host abstraction: a machine's identity, resources, and installed software.

Tools depend on this interface instead of branching on the operating system, so
Windows and Linux are handled in one place. Probing is best-effort: a missing
tool or a denied system call never fails the probe.
"""

from __future__ import annotations

import getpass
import locale
import os
import platform
import shutil
import socket
import subprocess
import sys
from abc import ABC, abstractmethod
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

COMMON_TOOLS: tuple[str, ...] = (
    "git",
    "uv",
    "pip",
    "pip3",
    "node",
    "npm",
    "docker",
    "curl",
    "wget",
    "ssh",
    "scp",
    "tar",
    "ffmpeg",
    "rg",
    "7z",
    "7za",
)
WINDOWS_TOOLS: tuple[str, ...] = (
    "powershell",
    "pwsh",
    "winget",
    "choco",
    "scoop",
    "schtasks",
    "reg",
    "sc",
    "tasklist",
)
LINUX_TOOLS: tuple[str, ...] = (
    "systemctl",
    "apt",
    "apt-get",
    "dnf",
    "pacman",
    "journalctl",
    "ss",
    "ps",
    "sudo",
    "pkexec",
)


@dataclass(frozen=True)
class Identity:
    user: str
    elevated: bool
    groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class SystemInfo:
    os: str
    version: str
    arch: str
    hostname: str
    distro: str | None = None
    kernel: str | None = None


@dataclass(frozen=True)
class DiskUsage:
    mount: str
    total: int
    free: int


@dataclass(frozen=True)
class Gpu:
    name: str
    memory: int | None = None


@dataclass(frozen=True)
class Resources:
    cpu_count: int
    memory_total: int
    memory_available: int
    disks: tuple[DiskUsage, ...] = ()
    gpus: tuple[Gpu, ...] = ()


@dataclass(frozen=True)
class Software:
    tools: dict[str, str] = field(default_factory=dict)
    package_managers: tuple[str, ...] = ()
    python_runtimes: tuple[str, ...] = ()


def probe_tools(names: tuple[str, ...]) -> dict[str, str]:
    found: dict[str, str] = {}
    for name in names:
        path = shutil.which(name)
        if path:
            found[name] = path
    return found


def python_runtimes() -> tuple[str, ...]:
    candidates = [sys.executable, shutil.which("python"), shutil.which("python3")]
    runtimes: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in runtimes:
            runtimes.append(candidate)
    return tuple(runtimes)


def probe_disks() -> tuple[DiskUsage, ...]:
    anchor = Path.cwd().anchor or os.sep
    with suppress(OSError):
        usage = shutil.disk_usage(anchor)
        return (DiskUsage(mount=anchor, total=usage.total, free=usage.free),)
    return ()


def probe_gpus() -> tuple[Gpu, ...]:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return ()
    try:
        completed = subprocess.run(
            [executable, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    gpus: list[Gpu] = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        name, _, memory = line.rpartition(",")
        if not name:
            name, memory = line, ""
        memory = memory.strip()
        gpus.append(
            Gpu(
                name=name.strip(),
                memory=int(memory) * 1024 * 1024 if memory.isdigit() else None,
            )
        )
    return tuple(gpus)


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False), errors="replace")


def run_command(argv: list[str], *, timeout: int = 60) -> tuple[bool, str]:
    """Run ``argv`` and return ``(succeeded, combined output)``.

    Output is decoded as UTF-8 with a locale fallback, so a tool that emits bytes
    in another code page (common on Windows) never crashes the probe.
    """
    try:
        completed = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = (completed.stdout or b"") + (completed.stderr or b"")
    return completed.returncode == 0, _decode(output).strip()


class Host(ABC):
    """The current machine. Subclasses fill in platform-specific probing."""

    name: str = "unknown"
    tool_names: tuple[str, ...] = COMMON_TOOLS
    package_manager_names: tuple[str, ...] = ()

    @abstractmethod
    def identity(self) -> Identity:
        raise NotImplementedError

    @abstractmethod
    def system(self) -> SystemInfo:
        raise NotImplementedError

    @abstractmethod
    def resources(self) -> Resources:
        raise NotImplementedError

    def software(self) -> Software:
        tools = probe_tools(self.tool_names)
        return Software(
            tools=tools,
            package_managers=tuple(
                name for name in self.package_manager_names if name in tools
            ),
            python_runtimes=python_runtimes(),
        )

    # -- process, service, package control --------------------------------
    def spawn_detached(
        self,
        argv: list[str],
        *,
        cwd: Path,
        log_path: Path,
        env: Mapping[str, str] | None = None,
    ) -> int:
        raise NotImplementedError

    def process_alive(self, pid: int) -> bool:
        raise NotImplementedError

    def kill_tree(self, pid: int) -> None:
        raise NotImplementedError

    def service_status(self, name: str) -> tuple[bool, str]:
        """Return ``(active, output)``."""
        raise NotImplementedError

    def service_action(self, name: str, action: str) -> tuple[bool, str]:
        """``action`` is start/stop/restart. Return ``(ok, output)``."""
        raise NotImplementedError

    def package_query(self, name: str) -> tuple[bool, str]:
        """Return ``(installed, output)``."""
        raise NotImplementedError

    def package_argv(self, action: str, names: list[str]) -> list[str]:
        """The package-manager argv for install/uninstall."""
        raise NotImplementedError

    def package_action(self, action: str, names: list[str]) -> tuple[bool, str]:
        """``action`` is install/uninstall. Return ``(ok, output)``."""
        return run_command(self.package_argv(action, names), timeout=900)

    def elevated_argv(self, argv: list[str]) -> list[str]:
        """argv that runs ``argv`` with the OS elevation prompt."""
        raise NotImplementedError


def current_user() -> str:
    with suppress(OSError):
        return getpass.getuser()
    return os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"


def hostname() -> str:
    with suppress(OSError):
        return socket.gethostname()
    return "unknown"


def machine_arch() -> str:
    return platform.machine() or "unknown"


__all__ = [
    "COMMON_TOOLS",
    "LINUX_TOOLS",
    "WINDOWS_TOOLS",
    "DiskUsage",
    "Gpu",
    "Host",
    "Identity",
    "Resources",
    "Software",
    "SystemInfo",
    "current_user",
    "hostname",
    "machine_arch",
    "probe_disks",
    "probe_gpus",
    "probe_tools",
    "python_runtimes",
    "run_command",
]
