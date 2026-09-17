"""Linux host probing (mainstream systemd distributions)."""

from __future__ import annotations

import importlib
import os
import platform
import shutil
import signal
import subprocess
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path

from tm.host.base import (
    COMMON_TOOLS,
    LINUX_TOOLS,
    Host,
    Identity,
    Resources,
    SystemInfo,
    current_user,
    hostname,
    machine_arch,
    probe_disks,
    probe_gpus,
    run_command,
)


def _distro_name() -> str | None:
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


def _memory() -> tuple[int, int]:
    total = 0
    available = 0
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        return 0, 0
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            value = rest.strip().split()[0]
            if value.isdigit():
                amount = int(value) * 1024
                if key == "MemTotal":
                    total = amount
                else:
                    available = amount
    return total, available


def _groups() -> tuple[str, ...]:
    getgroups = getattr(os, "getgroups", None)
    if getgroups is None:
        return ()
    try:
        module = importlib.import_module("grp")
    except ImportError:
        return ()
    getgrgid = getattr(module, "getgrgid", None)
    if getgrgid is None:
        return ()
    names: set[str] = set()
    for gid in getgroups():
        try:
            names.add(str(getgrgid(gid).gr_name))
        except KeyError:
            continue
    return tuple(sorted(names))


def _is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


class LinuxHost(Host):
    name = "linux"
    tool_names = COMMON_TOOLS + LINUX_TOOLS
    package_manager_names = ("apt", "apt-get", "dnf", "pacman")

    def identity(self) -> Identity:
        return Identity(user=current_user(), elevated=_is_root(), groups=_groups())

    def system(self) -> SystemInfo:
        release = platform.release()
        return SystemInfo(
            os="linux",
            version=release,
            arch=machine_arch(),
            hostname=hostname(),
            distro=_distro_name(),
            kernel=release,
        )

    def resources(self) -> Resources:
        total, available = _memory()
        return Resources(
            cpu_count=os.cpu_count() or 1,
            memory_total=total,
            memory_available=available,
            disks=probe_disks(),
            gpus=probe_gpus(),
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
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as log:
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=dict(env) if env is not None else None,
            )
        return int(proc.pid)

    def process_alive(self, pid: int) -> bool:
        if Path(f"/proc/{pid}").exists():
            return True
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return False
        return True

    def kill_tree(self, pid: int) -> None:
        killpg = getattr(os, "killpg", None)
        getpgid = getattr(os, "getpgid", None)
        sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
        if killpg is not None and getpgid is not None:
            with suppress(OSError, ProcessLookupError):
                killpg(getpgid(pid), sigkill)
                return
        with suppress(OSError, ProcessLookupError):
            os.kill(pid, sigkill)

    def service_status(self, name: str) -> tuple[bool, str]:
        if shutil.which("systemctl") is None:
            return False, "systemctl is not available"
        ok, output = run_command(["systemctl", "is-active", name])
        return ok, output or ("active" if ok else "inactive")

    def service_action(self, name: str, action: str) -> tuple[bool, str]:
        if shutil.which("systemctl") is None:
            return False, "systemctl is not available"
        return run_command(["systemctl", action, name], timeout=120)

    def _package_tool(self) -> str | None:
        for candidate in ("apt-get", "dnf"):
            if shutil.which(candidate):
                return candidate
        return None

    def package_query(self, name: str) -> tuple[bool, str]:
        if shutil.which("dpkg"):
            return run_command(["dpkg", "-s", name])
        if shutil.which("rpm"):
            return run_command(["rpm", "-q", name])
        return False, "no supported package manager found"

    def package_argv(self, action: str, names: list[str]) -> list[str]:
        tool = self._package_tool() or "apt-get"
        verb = "remove" if action == "uninstall" else "install"
        return [tool, verb, "-y", *names]

    def package_action(self, action: str, names: list[str]) -> tuple[bool, str]:
        if self._package_tool() is None:
            return False, "no supported package manager found"
        return run_command(self.package_argv(action, names), timeout=900)

    def elevated_argv(self, argv: list[str]) -> list[str]:
        if shutil.which("sudo"):
            return ["sudo", "--", *argv]
        if shutil.which("pkexec"):
            return ["pkexec", *argv]
        return argv


__all__ = ["LinuxHost"]
