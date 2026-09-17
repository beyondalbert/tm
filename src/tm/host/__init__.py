"""Host abstraction: platform-specific probing behind one interface."""

from __future__ import annotations

from tm.host.base import (
    DiskUsage,
    Gpu,
    Host,
    Identity,
    Resources,
    Software,
    SystemInfo,
    probe_disks,
    probe_gpus,
    probe_tools,
    python_runtimes,
)
from tm.host.detect import detect_host
from tm.host.linux import LinuxHost
from tm.host.prompt import human_bytes, render_environment
from tm.host.windows import WindowsHost

__all__ = [
    "DiskUsage",
    "Gpu",
    "Host",
    "Identity",
    "LinuxHost",
    "Resources",
    "Software",
    "SystemInfo",
    "WindowsHost",
    "detect_host",
    "human_bytes",
    "probe_disks",
    "probe_gpus",
    "probe_tools",
    "python_runtimes",
    "render_environment",
]
