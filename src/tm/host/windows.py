"""Windows host probing."""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

from tm.host.base import (
    COMMON_TOOLS,
    WINDOWS_TOOLS,
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


def _is_admin() -> bool:
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return False
    try:
        return bool(windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _memory() -> tuple[int, int]:
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return 0, 0

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    try:
        ok = windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    except (AttributeError, OSError):
        return 0, 0
    if not ok:
        return 0, 0
    return int(status.ullTotalPhys), int(status.ullAvailPhys)


class WindowsHost(Host):
    name = "windows"
    tool_names = COMMON_TOOLS + WINDOWS_TOOLS
    package_manager_names = ("winget", "choco", "scoop")

    def identity(self) -> Identity:
        return Identity(user=current_user(), elevated=_is_admin(), groups=())

    def system(self) -> SystemInfo:
        return SystemInfo(
            os="windows",
            version=platform.release(),
            arch=machine_arch(),
            hostname=hostname(),
            kernel=platform.version(),
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
        # DETACHED_PROCESS (0x08) makes some console programs exit at once, so
        # use CREATE_NO_WINDOW plus a new process group instead.
        create_no_window = 0x08000000
        new_process_group = 0x00000200
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as log:
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=create_no_window | new_process_group,
                env=dict(env) if env is not None else None,
            )
        return int(proc.pid)

    def process_alive(self, pid: int) -> bool:
        tasklist = shutil.which("tasklist")
        if tasklist is None:
            return False
        ok, output = run_command([tasklist, "/FI", f"PID eq {pid}", "/NH"])
        return ok and str(pid) in output

    def kill_tree(self, pid: int) -> None:
        taskkill = shutil.which("taskkill")
        if taskkill is not None:
            run_command([taskkill, "/PID", str(pid), "/T", "/F"])

    def service_status(self, name: str) -> tuple[bool, str]:
        return run_command(["sc", "query", name])

    def service_action(self, name: str, action: str) -> tuple[bool, str]:
        verb = {"start": "start", "stop": "stop", "restart": "restart"}.get(action, action)
        if verb == "restart":
            run_command(["sc", "stop", name])
            return run_command(["sc", "start", name])
        return run_command(["sc", verb, name])

    def package_query(self, name: str) -> tuple[bool, str]:
        if shutil.which("winget") is None:
            return False, "winget is not installed"
        ok, output = run_command(["winget", "list", "--id", name, "--exact"])
        return ok and name.lower() in output.lower(), output

    def package_argv(self, action: str, names: list[str]) -> list[str]:
        winget = shutil.which("winget") or "winget"
        verb = "uninstall" if action == "uninstall" else "install"
        return [
            winget,
            verb,
            "--id",
            names[0],
            "--exact",
            "--silent",
            "--accept-source-agreements",
            "--accept-package-agreements",
        ]

    def package_action(self, action: str, names: list[str]) -> tuple[bool, str]:
        if shutil.which("winget") is None:
            return False, "winget is not installed"
        return run_command(self.package_argv(action, names), timeout=900)

    def elevated_argv(self, argv: list[str]) -> list[str]:
        quote = chr(39)
        executable = argv[0].replace(quote, quote * 2)
        arguments = ",".join(
            quote + arg.replace(quote, quote * 2) + quote for arg in argv[1:]
        )
        script = (
            f"Start-Process -Verb RunAs -Wait -FilePath '{executable}' "
            f"-ArgumentList @({arguments})"
        )
        return ["powershell", "-NoProfile", "-Command", script]


__all__ = ["WindowsHost"]
