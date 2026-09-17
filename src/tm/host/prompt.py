"""Render a host description for the prompt and the ``system_info`` tool."""

from __future__ import annotations

import platform
import sys

from tm.host.base import Host


def human_bytes(value: int) -> str:
    if value <= 0:
        return "unknown"
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def render_environment(host: Host, *, full: bool = False) -> str:
    identity = host.identity()
    system = host.system()
    resources = host.resources()
    software = host.software()

    machine = system.os.title()
    if system.distro:
        machine = f"{machine} {system.distro}"
    if system.os == "windows" and system.version:
        machine = f"{machine} {system.version}"

    elevated = "elevated" if identity.elevated else "not elevated"
    lines = [
        "Environment:",
        f"- Machine: {machine}, {system.arch}, host={system.hostname}",
        f"- User: {identity.user} ({elevated})",
    ]

    if resources.memory_total:
        memory = (
            f"{human_bytes(resources.memory_available)} free "
            f"of {human_bytes(resources.memory_total)}"
        )
    else:
        memory = "unknown"
    lines.append(f"- CPU: {resources.cpu_count} cores, memory {memory}")

    if resources.disks:
        disks = "; ".join(
            f"{disk.mount} {human_bytes(disk.free)} free of {human_bytes(disk.total)}"
            for disk in resources.disks
        )
        lines.append(f"- Disk: {disks}")
    if resources.gpus:
        gpus = ", ".join(
            gpu.name + (f" ({human_bytes(gpu.memory)})" if gpu.memory else "")
            for gpu in resources.gpus
        )
        lines.append(f"- GPU: {gpus}")

    lines.append(f"- Python: {sys.executable} ({platform.python_version()})")
    if software.tools:
        lines.append("- Tools: " + ", ".join(software.tools))
    if software.package_managers:
        lines.append("- Package managers: " + ", ".join(software.package_managers))

    if full:
        lines.append("- Tool paths:")
        lines.extend(f"    {name}: {path}" for name, path in software.tools.items())
        lines.append("- Python runtimes: " + ", ".join(software.python_runtimes))
    return "\n".join(lines)


__all__ = ["human_bytes", "render_environment"]
