"""Extensions: user Python modules that register tools, listeners, and commands.

An extension file (``*.py``) defines a ``setup(api)`` (or ``default``) function
called with an :class:`ExtensionAPI`. Extensions run with full process
permissions, exactly like the rest of the agent.
"""

from __future__ import annotations

import importlib.util
import inspect
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from tm.core.events import AgentEvent
from tm.tools.base import Tool

CommandHandler = Callable[[str], Any]
EventListener = Callable[[AgentEvent], Coroutine[Any, Any, None]]


class ExtensionAPI:
    def __init__(self) -> None:
        self.tools: list[Tool] = []
        self.listeners: list[EventListener] = []
        self.commands: dict[str, CommandHandler] = {}
        self.sources: list[Path] = []

    def register_tool(self, tool: Tool) -> None:
        self.tools.append(tool)

    def on(self, event_type: str, handler: Callable[[AgentEvent], Any]) -> None:
        async def listener(event: AgentEvent) -> None:
            if event.type != event_type:
                return
            result = handler(event)
            if inspect.isawaitable(result):
                await result

        self.listeners.append(listener)

    def register_command(self, name: str, handler: CommandHandler) -> None:
        self.commands[name] = handler


def extension_roots(cwd: Path, config_dir: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    if config_dir is not None:
        roots.append(config_dir / "extensions")
    roots.append(cwd / ".aiagent" / "extensions")
    return roots


def _load_module(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(f"tm_extension_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load extension: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_extensions(roots: list[Path]) -> ExtensionAPI:
    api = ExtensionAPI()
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.py")):
            if path.name.startswith("_"):
                continue
            module = _load_module(path)
            setup = getattr(module, "setup", None) or getattr(module, "default", None)
            if callable(setup):
                setup(api)
                api.sources.append(path)
    return api


__all__ = [
    "ExtensionAPI",
    "CommandHandler",
    "EventListener",
    "extension_roots",
    "load_extensions",
]
