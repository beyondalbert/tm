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


async def test_console_ui_strips_terminal_escapes_from_tool_output() -> None:
    ui, stream = make_ui()
    await ui.handle(
        ToolExecutionEndEvent(
            tool_call_id="1",
            tool_name="shell",
            is_error=False,
            output="\x1b[32mok\x1b[0m\n\x1b[38;5;32m",
        )
    )
    assert "\x1b" not in stream.getvalue()
    assert "ok" in stream.getvalue()


async def test_console_ui_strips_terminal_escapes_from_a_reply() -> None:
    from tm.ai.types import AssistantMessage, TextContent, Usage
    from tm.core.events import MessageEndEvent, MessageStartEvent

    ui, stream = make_ui()
    message = AssistantMessage(
        model="m",
        content=[TextContent(text="\x1b[31mred\x1b[0m and [555;5;32Mgone")],
        stop_reason="stop",
        usage=Usage(input=1, output=1, total=2),
    )
    await ui.handle(MessageStartEvent(message=message))
    await ui.handle(MessageEndEvent(message=message))
    assert "\x1b" not in stream.getvalue()
    assert "[555;5;32M" not in stream.getvalue()
    assert "red and gone" in stream.getvalue()
