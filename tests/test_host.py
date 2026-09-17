from __future__ import annotations

from tm.host import detect_host, human_bytes, render_environment
from tm.host.base import (
    DiskUsage,
    Gpu,
    Host,
    Identity,
    Resources,
    Software,
    SystemInfo,
)


class FakeHost(Host):
    name = "fake"

    def identity(self) -> Identity:
        return Identity(user="tester", elevated=False)

    def system(self) -> SystemInfo:
        return SystemInfo(
            os="linux",
            version="6.1",
            arch="x86_64",
            hostname="fakebox",
            distro="Ubuntu 24.04",
            kernel="6.1",
        )

    def resources(self) -> Resources:
        gib = 1024**3
        return Resources(
            cpu_count=8,
            memory_total=16 * gib,
            memory_available=8 * gib,
            disks=(DiskUsage(mount="/", total=500 * gib, free=100 * gib),),
            gpus=(Gpu(name="RTX 4090", memory=24 * gib),),
        )

    def software(self) -> Software:
        return Software(
            tools={"git": "/usr/bin/git", "uv": "/usr/bin/uv"},
            package_managers=("apt",),
            python_runtimes=("/usr/bin/python3",),
        )


def test_human_bytes() -> None:
    assert human_bytes(0) == "unknown"
    assert human_bytes(1024) == "1.0 KB"
    assert human_bytes(16 * 1024**3) == "16.0 GB"


def test_render_environment_contains_machine_facts() -> None:
    text = render_environment(FakeHost())
    assert "Ubuntu 24.04" in text
    assert "tester (not elevated)" in text
    assert "8 cores" in text
    assert "RTX 4090" in text
    assert "git, uv" in text
    assert "apt" in text


def test_render_environment_full_lists_tool_paths() -> None:
    text = render_environment(FakeHost(), full=True)
    assert "/usr/bin/git" in text


def test_detect_host_returns_a_host() -> None:
    assert isinstance(detect_host(), Host)


def test_live_probe_does_not_crash() -> None:
    host = detect_host()
    assert host.identity().user
    assert host.system().os
    assert host.resources().cpu_count >= 1
