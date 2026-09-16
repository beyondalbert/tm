"""Console renderer for agent events."""

from __future__ import annotations

import json

from rich.console import Console

from tm.ai.types import AssistantMessage
from tm.core.events import (
    AgentEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)

_MAX_TOOL_OUTPUT = 4000


class ConsoleAgentUI:
    def __init__(self, console: Console) -> None:
        self.console = console
        self._text_printed = 0
        self._thinking_printed = 0
        self._text_started = False

    async def handle(self, event: AgentEvent) -> None:
        if isinstance(event, MessageStartEvent):
            if isinstance(event.message, AssistantMessage):
                self._text_printed = 0
                self._thinking_printed = 0
                self._text_started = False
        elif isinstance(event, MessageUpdateEvent):
            self._render_assistant(event.message)
        elif isinstance(event, MessageEndEvent):
            message = event.message
            if isinstance(message, AssistantMessage):
                self._render_assistant(message)
                if self._text_started or self._thinking_printed:
                    self.console.print()
                if message.stop_reason == "error":
                    self.console.print(
                        f"[red]error: {message.error_message or 'unknown error'}[/red]"
                    )
                elif message.stop_reason == "aborted":
                    self.console.print("[yellow]aborted[/yellow]")
                if message.usage and (message.usage.input or message.usage.output):
                    self.console.print(
                        f"[dim]tokens: in={message.usage.input} out={message.usage.output}[/dim]"
                    )
        elif isinstance(event, ToolExecutionStartEvent):
            arguments = json.dumps(event.arguments, ensure_ascii=False)
            if len(arguments) > 200:
                arguments = arguments[:200] + "..."
            self.console.print(f"[yellow]> {event.tool_name}[/yellow] [dim]{arguments}[/dim]")
        elif isinstance(event, ToolExecutionEndEvent):
            output = event.output
            if len(output) > _MAX_TOOL_OUTPUT:
                output = output[:_MAX_TOOL_OUTPUT] + "\n... (trimmed)"
            style = "red" if event.is_error else "dim"
            self.console.print(f"[{style}]{output}[/{style}]")

    def _render_assistant(self, message: AssistantMessage) -> None:
        thinking = message.thinking()
        if len(thinking) > self._thinking_printed:
            if self._thinking_printed == 0 and not self._text_started:
                self.console.print("[dim]thinking[/dim]")
            self.console.print(
                thinking[self._thinking_printed :],
                end="",
                style="dim",
                markup=False,
                highlight=False,
            )
            self._thinking_printed = len(thinking)

        text = message.text()
        if len(text) > self._text_printed:
            if self._thinking_printed and not self._text_started:
                self.console.print()
            self.console.print(
                text[self._text_printed :], end="", markup=False, highlight=False
            )
            self._text_printed = len(text)
            self._text_started = True


__all__ = ["ConsoleAgentUI"]
