"""Run ``tm im`` detached in the background.

Every instance is isolated on disk by its config file. ``tm im start --config
a.toml`` and ``--config b.toml`` use separate directories (state, sessions,
pidfile, log), so several accounts can run on one machine without interfering.
``tm im start`` re-launches the normal ``run`` command as a detached child
process (no console, output appended to a log file) and records its pid.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

_CREATE_NEW_PROCESS_GROUP = 0x00000200
_DETACHED_PROCESS = 0x00000008
_MAX_LOG_BYTES = 5 * 1024 * 1024


def pid_path(base: Path) -> Path:
    return base / "im.pid"


def log_path(base: Path) -> Path:
    return base / "im.log"


# ---------------------------------------------------------------------------
# pid file
# ---------------------------------------------------------------------------
def read_pid(base: Path) -> int | None:
    path = pid_path(base)
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def write_pid(base: Path, pid: int) -> None:
    path = pid_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def clear_pid(base: Path) -> None:
    with contextlib.suppress(OSError):
        pid_path(base).unlink()


def is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        # ``ctypes.windll`` is Windows-only, and the stubs omit it elsewhere.
        kernel32 = cast(Any, ctypes).windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def running_pid(base: Path) -> int | None:
    pid = read_pid(base)
    if pid is not None and is_alive(pid):
        return pid
    return None


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------
def start_background(base: Path, forwarded: list[str]) -> int:
    base.mkdir(parents=True, exist_ok=True)
    _rotate_log(base)
    command = [sys.executable, "-m", "tm.im.daemon", "run", *forwarded]
    with open(log_path(base), "ab") as log:
        if os.name == "nt":
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                close_fds=True,
                creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
            )
        else:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                close_fds=True,
                start_new_session=True,
            )
    write_pid(base, process.pid)
    return process.pid


def stop_background(base: Path) -> int | None:
    pid = running_pid(base)
    if pid is None:
        clear_pid(base)
        return None
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        import signal

        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
    clear_pid(base)
    return pid


def _rotate_log(base: Path) -> None:
    path = log_path(base)
    try:
        if path.exists() and path.stat().st_size > _MAX_LOG_BYTES:
            path.replace(path.with_suffix(".log.1"))
    except OSError:
        pass


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------
def read_tail(base: Path, lines: int) -> list[str]:
    path = log_path(base)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        content = handle.readlines()
    return [line.rstrip("\n") for line in content[-lines:]]


def follow(base: Path, lines: int, sleep: float = 0.5) -> None:
    path = log_path(base)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        if lines > 0:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - lines * 200)
            handle.seek(start)
            handle.readline()  # drop a partial line
        while True:
            line = handle.readline()
            if line:
                sys.stdout.write(line)
                sys.stdout.flush()
            else:
                time.sleep(sleep)


def wait_alive(pid: int, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_alive(pid):
            return False
        time.sleep(0.2)
    return True


# ---------------------------------------------------------------------------
# detached entry point: ``python -m tm.im.daemon run ...``
# ---------------------------------------------------------------------------
def main() -> None:
    os.environ["TM_IM_DAEMON"] = "1"
    sys.argv = ["tm", "im", *sys.argv[1:]]
    from tm.cli.main import run_cli

    run_cli()


if __name__ == "__main__":
    main()


__all__ = [
    "clear_pid",
    "follow",
    "is_alive",
    "log_path",
    "main",
    "pid_path",
    "read_pid",
    "read_tail",
    "running_pid",
    "start_background",
    "stop_background",
    "wait_alive",
    "write_pid",
]
