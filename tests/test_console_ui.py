from __future__ import annotations

import io

from rich.console import Console

from tm.cli.console_ui import ConsoleAgentUI
from tm.core.events import AgentEndEvent, ToolExecutionEndEvent, ToolExecutionStartEvent


def make_ui() -> tuple[ConsoleAgentUI, io.StringIO]:
    stream = io.StringIO()
    console = Console(file=stream, width=200, force_terminal=False, no_color=True)
    return ConsoleAgentUI(console), stream


async def test_console_ui_reports_the_turn_limit() -> None:
    ui, stream = make_ui()
    await ui.handle(AgentEndEvent(messages=[], stop_reason="max_turns"))
    assert "turn limit" in stream.getvalue()


async def test_console_ui_is_quiet_on_a_normal_stop() -> None:
    ui, stream = make_ui()
    await ui.handle(AgentEndEvent(messages=[], stop_reason="stop"))
    assert stream.getvalue().strip() == ""


async def test_console_ui_does_not_parse_markup_in_tool_output() -> None:
    ui, stream = make_ui()
    await ui.handle(
        ToolExecutionEndEvent(
            tool_call_id="1",
            tool_name="read",
            is_error=False,
            output="closing tag [/{id}] must stay literal",
        )
    )
    assert "[/{id}]" in stream.getvalue()


async def test_console_ui_does_not_parse_markup_in_tool_arguments() -> None:
    ui, stream = make_ui()
    await ui.handle(
        ToolExecutionStartEvent(
            tool_call_id="1", tool_name="read", arguments={"path": "a[/{id}].txt"}
        )
    )
    assert "{id}" in stream.getvalue()
