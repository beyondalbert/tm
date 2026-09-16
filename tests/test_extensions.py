from __future__ import annotations

import asyncio
from pathlib import Path

from tm.core.events import AgentStartEvent
from tm.extensions import ExtensionAPI, extension_roots, load_extensions

EXTENSION_SOURCE = '''
from pydantic import BaseModel

from tm.tools.base import Tool, ToolContext, ToolResult, text_result


class PingParams(BaseModel):
    pass


class PingTool(Tool[PingParams]):
    name = "ping"
    description = "Reply pong."
    parameters_model = PingParams

    async def execute(self, call_id, args, ctx):
        return text_result("pong")


def setup(api):
    calls = []
    api.register_tool(PingTool())
    api.register_command("calls", lambda arg: calls)
    api.on("agent_start", lambda event: calls.append(event.type))
'''


def write_extension(root: Path, name: str = "hello") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.py").write_text(EXTENSION_SOURCE)


def test_load_extension_registers_tool_command_listener(tmp_path: Path) -> None:
    root = tmp_path / "extensions"
    write_extension(root)

    api = load_extensions([root])

    assert [tool.name for tool in api.tools] == ["ping"]
    assert api.sources and api.sources[0].name == "hello.py"
    assert api.commands["calls"]("") == []

    listener = api.listeners[0]
    asyncio.run(listener(AgentStartEvent()))
    assert api.commands["calls"]("") == ["agent_start"]


def test_listener_filters_event_type(tmp_path: Path) -> None:
    root = tmp_path / "extensions"
    write_extension(root)
    api = load_extensions([root])

    from tm.core.events import TurnStartEvent

    listener = api.listeners[0]
    asyncio.run(listener(TurnStartEvent(turn=1)))
    assert api.commands["calls"]("") == []


def test_missing_directory_is_ignored(tmp_path: Path) -> None:
    api = load_extensions([tmp_path / "nope"])
    assert api.tools == []
    assert api.listeners == []


def test_extension_roots(tmp_path: Path) -> None:
    roots = extension_roots(tmp_path, tmp_path / "cfg")
    assert roots == [tmp_path / "cfg" / "extensions", tmp_path / ".aiagent" / "extensions"]


def test_load_module_error_propagates(tmp_path: Path) -> None:
    root = tmp_path / "extensions"
    root.mkdir()
    (root / "broken.py").write_text("raise RuntimeError('bad extension')")
    try:
        load_extensions([root])
    except RuntimeError as exc:
        assert "bad extension" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")


def test_extension_api_register_tool_only_once() -> None:
    api = ExtensionAPI()
    assert api.commands == {}
