"""Self-update: check PyPI for a newer release and upgrade TM in place.

Designed to work without uv: it prefers the installer that owns the current
environment (uv/pipx) when available, and otherwise uses pip. On Windows the
running ``tm.exe`` cannot be replaced in place, so the upgrade runs in a small
detached helper that waits for this process to exit first.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path

from tm.config import config_dir

PACKAGE = "the-machine"
PYPI_JSON = f"https://pypi.org/pypi/{PACKAGE}/json"


def _fetch(url: str, timeout: float) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, OSError, TimeoutError):
        return None


def latest_version(timeout: float = 10.0) -> str | None:
    """The latest version on PyPI, or None when it cannot be reached."""
    payload = _fetch(PYPI_JSON, timeout)
    if payload is None:
        return None
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    versions: list[str] = []
    info = data.get("info")
    if isinstance(info, dict) and isinstance(info.get("version"), str):
        versions.append(str(info["version"]))
    releases = data.get("releases")
    if isinstance(releases, dict):
        versions.extend(key for key in releases if isinstance(key, str))
    if not versions:
        return None
    # info.version can lag the CDN cache; the release list is the source of truth.
    return max(versions, key=parse_version)


def parse_version(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in text.strip().split("."):
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def _has_pip() -> bool:
    return importlib.util.find_spec("pip") is not None


def upgrade_argv(prefix: Path | None = None) -> list[str] | None:
    """The command that upgrades the package in the current environment."""
    prefix = prefix or Path(sys.prefix)
    uv = shutil.which("uv")
    if uv and (prefix / "uv-receipt.toml").is_file():
        return [uv, "tool", "upgrade", PACKAGE]
    if (prefix / "pipx_metadata.json").is_file():
        pipx = shutil.which("pipx")
        if pipx:
            return [pipx, "upgrade", PACKAGE]
    if _has_pip():
        return [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE]
    if uv:
        return [uv, "tool", "upgrade", PACKAGE]
    return None


def _alive(pid: int) -> bool:
    if os.name == "nt":
        try:
            output = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
            ).stdout
        except OSError:
            return False
        return str(pid) in output
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_and_upgrade(pid: int, argv: Sequence[str]) -> None:
    """Run in the detached helper: wait for the parent, then upgrade."""
    deadline = time.time() + 300
    while time.time() < deadline and _alive(pid):
        time.sleep(0.5)
    subprocess.call(list(argv))


def _start_background_upgrade(argv: Sequence[str]) -> Path:
    log_path = config_dir() / "update.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = (
        "import sys; from tm.update import _wait_and_upgrade; "
        "_wait_and_upgrade(int(sys.argv[1]), sys.argv[2:])"
    )
    creationflags = 0x08000000 | 0x00000200 if os.name == "nt" else 0
    with log_path.open("ab") as log:
        subprocess.Popen(
            [sys.executable, "-c", command, str(os.getpid()), *argv],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
    return log_path


def _current_version() -> str:
    from tm import __version__

    return __version__


def run_update(
    out: Callable[[str], None] = print,
    *,
    prefix: Path | None = None,
    background: bool | None = None,
) -> int:
    """Check PyPI and upgrade. Returns a process exit code."""
    current = _current_version()
    latest = latest_version()
    if latest is None:
        out("could not reach PyPI to check for updates")
        return 1
    if not is_newer(latest, current):
        out(f"TM {current} is up to date (latest {latest})")
        return 0

    argv = upgrade_argv(prefix)
    if argv is None:
        out("pip is not available; bootstrapping it with ensurepip")
        code = subprocess.call([sys.executable, "-m", "ensurepip", "--upgrade"])
        if code != 0:
            out(f"could not bootstrap pip; run manually: pip install --upgrade {PACKAGE}")
            return code
        argv = [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE]

    if background is None:
        background = os.name == "nt"
    if background:
        log_path = _start_background_upgrade(argv)
        out(
            f"updating TM {current} -> {latest} in the background; "
            f"exit tm, then check {log_path}"
        )
        return 0

    out(f"updating TM {current} -> {latest}")
    return subprocess.call(argv)


__all__ = [
    "PACKAGE",
    "is_newer",
    "latest_version",
    "parse_version",
    "run_update",
    "upgrade_argv",
]
