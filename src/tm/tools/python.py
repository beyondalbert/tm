"""The ``python`` tool: a durable, verifiable fallback for goals shell cannot express.

The code is saved to a workspace script (so it can be re-run and edited), run
with a configurable interpreter, streamed, time-limited, truncated, and it
pre-installs declared packages when auto-install is enabled.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

from pydantic import BaseModel

from tm.config import config_dir, load_settings
from tm.tools.base import Tool, ToolContext, ToolResult, text_result
from tm.tools.subprocess_utils import stream_command
from tm.tools.truncate import truncate_text

_PACKAGE_PROBE = (
    "import importlib.util, sys\n"
    "missing = [n for n in sys.argv[1:] if importlib.util.find_spec(n) is None]\n"
    "print('\\n'.join(missing))\n"
)


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return (cleaned or "script")[:40]


def script_path(workspace: Path, description: str | None, code: str) -> Path:
    digest = hashlib.sha1(code.encode("utf-8")).hexdigest()[:8]
    return workspace / "scripts" / f"{_slug(description or 'script')}-{digest}.py"


class PythonParams(BaseModel):
    code: str
    description: str | None = None
    packages: list[str] = []
    timeout: int | None = None
    cwd: str | None = None


class PythonTool(Tool[PythonParams]):
    name = "python"
    description = (
        "Run a Python script and return its combined stdout/stderr. Use it for "
        "anything shell one-liners cannot express: parse data, drive a software "
        "API, or build and check a service. The code is saved to a workspace file "
        "so it can be re-run or edited. `packages` lists pip packages the code "
        "needs; missing ones are installed first when auto-install is enabled."
    )
    parameters_model = PythonParams
    execution_mode = "sequential"
    replay_safe = False

    def __init__(
        self,
        *,
        executable: str | None = None,
        workspace: Path | None = None,
        auto_install: bool | None = None,
        timeout: int | None = None,
    ) -> None:
        self._executable = executable
        self._workspace = workspace
        self._auto_install = auto_install
        self._timeout = timeout

    def _options(self) -> tuple[str, Path, bool, int]:
        settings = load_settings()
        executable = self._executable or settings.python_executable or sys.executable
        if self._workspace is not None:
            workspace = self._workspace
        elif settings.workspace_dir:
            workspace = Path(settings.workspace_dir)
        else:
            workspace = config_dir() / "workspace"
        auto_install = (
            settings.auto_install if self._auto_install is None else self._auto_install
        )
        timeout = self._timeout if self._timeout is not None else settings.python_timeout
        return executable, workspace, auto_install, timeout

    def _cwd(self, args: PythonParams, ctx: ToolContext) -> Path:
        cwd = Path(args.cwd) if args.cwd else ctx.cwd
        if not cwd.is_absolute():
            cwd = ctx.cwd / cwd
        return cwd

    async def execute(
        self, call_id: str, args: PythonParams, ctx: ToolContext
    ) -> ToolResult:
        executable, workspace, auto_install, default_timeout = self._options()
        cwd = self._cwd(args, ctx)
        if not cwd.exists():
            return text_result(f"Working directory does not exist: {cwd}", is_error=True)

        script = script_path(workspace, args.description, args.code)
        try:
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text(args.code, encoding="utf-8")
        except OSError as exc:
            return text_result(f"Could not write script {script}: {exc}", is_error=True)

        timeout = args.timeout or default_timeout

        if args.packages and auto_install:
            missing = await self._missing_packages(executable, args.packages, cwd, ctx)
            if missing:
                install = await stream_command(
                    [executable, "-m", "pip", "install", *missing],
                    cwd=cwd,
                    timeout=max(timeout, 300),
                    signal=ctx.signal,
                    on_update=ctx.on_update,
                )
                if install.returncode != 0:
                    return text_result(
                        _format("Package install failed", install, script),
                        is_error=True,
                    )

        try:
            result = await stream_command(
                [executable, str(script)],
                cwd=cwd,
                timeout=timeout,
                signal=ctx.signal,
                on_update=ctx.on_update,
            )
        except OSError as exc:
            return text_result(f"Could not start Python ({executable}): {exc}", is_error=True)

        return text_result(
            _format("", result, script, timeout=timeout),
            is_error=result.aborted or result.timed_out or result.returncode != 0,
        )

    async def _missing_packages(
        self, executable: str, packages: list[str], cwd: Path, ctx: ToolContext
    ) -> list[str]:
        result = await stream_command(
            [executable, "-c", _PACKAGE_PROBE, *packages],
            cwd=cwd,
            timeout=30,
            signal=ctx.signal,
        )
        if result.returncode != 0:
            return list(packages)
        return [line.strip() for line in result.output.splitlines() if line.strip()]


def _format(prefix: str, result, script: Path, timeout: int | None = None) -> str:
    output, truncated = truncate_text(result.output)
    notes = []
    if result.aborted:
        notes.append("aborted")
    elif result.timed_out:
        notes.append(f"timed out after {timeout}s")
    notes.append(f"exit code {result.returncode}")
    notes.append(f"script {script}")
    head = f"{prefix}\n" if prefix else ""
    summary = head + output + ("\n" if output else "") + f"[{', '.join(notes)}]"
    if truncated:
        summary += "\n... (output truncated)"
    return summary


__all__ = ["PythonParams", "PythonTool", "script_path"]
