"""Self-update: check PyPI for a newer release and upgrade TM in place.

Designed to work without uv: it prefers the installer that owns the current
environment (uv/pipx) when available, and otherwise uses pip. On Windows the
running ``tm.exe`` cannot be replaced in place, so the upgrade runs in a small
detached helper that waits for this process to exit first.
"""

from __future__ import annotations

import contextlib
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
from tm.host.base import run_command

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
    """Whether ``pid`` is still running.

    The Windows probe runs through ``run_command``: tasklist prints a localized
    message when nothing matches, and decoding those bytes as UTF-8 under
    Python 3.15's default UTF-8 mode used to fail and leave ``stdout`` as None.
    """
    if os.name == "nt":
        tasklist = shutil.which("tasklist")
        if tasklist is None:
            return False
        ok, output = run_command([tasklist, "/FI", f"PID eq {pid}", "/NH"])
        return ok and str(pid) in output
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_and_upgrade(pid: int, argv: Sequence[str], log_path: str) -> None:
    """Run in the detached helper: wait for the parent, then upgrade.

    Output is streamed to the console (when one is still attached) and appended
    to ``log_path``, so a failure is visible instead of only being logged.
    """
    deadline = time.time() + 300
    while time.time() < deadline and _alive(pid):
        time.sleep(0.5)

    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log:

        def emit(text: str) -> None:
            log.write(text)
            log.flush()
            with contextlib.suppress(OSError, ValueError):
                sys.stdout.write(text)
                sys.stdout.flush()

        emit(f"\n[tm update] {' '.join(argv)}\n")
        try:
            proc = subprocess.Popen(
                list(argv),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
            )
        except OSError as exc:
            emit(f"[tm update] could not run the updater: {exc}\n")
            emit(f"[tm update] run manually: {' '.join(argv)}\n")
            return
        assert proc.stdout is not None
        for line in proc.stdout:
            emit(line)
        code = proc.wait()
        if code == 0:
            emit("[tm update] update complete\n")
        else:
            emit(f"[tm update] update failed (exit code {code})\n")


def _start_background_upgrade(argv: Sequence[str]) -> Path:
    log_path = config_dir() / "update.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = (
        "import sys; from tm.update import _wait_and_upgrade; "
        "_wait_and_upgrade(int(sys.argv[1]), sys.argv[2:-1], sys.argv[-1])"
    )
    # Keep the console attached so progress and errors are visible; the helper
    # waits for this process to exit before replacing the running executable.
    creationflags = 0x00000200 if os.name == "nt" else 0
    subprocess.Popen(
        [sys.executable, "-c", command, str(os.getpid()), *argv, str(log_path)],
        stdin=subprocess.DEVNULL,
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
        out("could not reach PyPI to check for updates (network or DNS error)")
        out(f"upgrade manually: pip install --upgrade {PACKAGE}")
        return 1
    if not is_newer(latest, current):
        if parse_version(current) > parse_version(latest):
            out(
                f"TM {current} is up to date "
                f"(the PyPI index still reports {latest}; it can lag briefly)"
            )
        else:
            out(f"TM {current} is up to date (latest {latest})")
        return 0

    argv = upgrade_argv(prefix)
    if argv is None:
        out("pip is not available; bootstrapping it with ensurepip")
        try:
            code = subprocess.call([sys.executable, "-m", "ensurepip", "--upgrade"])
        except OSError as exc:
            out(f"could not run ensurepip: {exc}")
            out(f"run manually: pip install --upgrade {PACKAGE}")
            return 1
        if code != 0:
            out(f"could not bootstrap pip; run manually: pip install --upgrade {PACKAGE}")
            return code
        argv = [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE]

    command = " ".join(argv)
    if background is None:
        background = os.name == "nt"
    if background:
        try:
            log_path = _start_background_upgrade(argv)
        except OSError as exc:
            out(f"could not start the updater: {exc}")
            out(f"run manually: {command}")
            return 1
        out(f"updating TM {current} -> {latest} in the background ({command})")
        out(f"progress and errors are printed here and appended to {log_path}")
        return 0

    out(f"updating TM {current} -> {latest}: {command}")
    try:
        code = subprocess.call(argv)
    except OSError as exc:
        out(f"could not run the updater: {exc}")
        out(f"run manually: {command}")
        return 1
    if code == 0:
        out("update complete; restart tm to use the new version")
    else:
        out(f"update failed (exit code {code}); see the output above")
    return code


__all__ = [
    "PACKAGE",
    "is_newer",
    "latest_version",
    "parse_version",
    "run_update",
    "upgrade_argv",
]
