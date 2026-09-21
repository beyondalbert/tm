from __future__ import annotations

import io

from rich.console import Console

from tm.cli.console_ui import ConsoleAgentUI
from tm.core.events import AgentEndEvent


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
