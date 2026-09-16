"""Textual terminal UI for TM."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from tm.ai.types import AssistantMessage, Model
from tm.cli.commands import ExitSignal
from tm.core.agent import Agent
from tm.core.events import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)
from tm.core.session import SessionInfo
from tm.permissions import ApprovalOutcome


class PermissionScreen(ModalScreen[ApprovalOutcome]):
    """Modal that asks the user to approve a sensitive action."""

    BINDINGS = [Binding("escape", "deny", "Deny")]

    def __init__(self, description: str, reason: str) -> None:
        super().__init__()
        self._description = description
        self._reason = reason

    def compose(self) -> ComposeResult:
        with Vertical(id="perm-box"):
            yield Label("Permission required", id="perm-title")
            yield Static(f"{self._description}\n{self._reason}", id="perm-body")
            with Horizontal(id="perm-buttons"):
                yield Button("Allow", id="allow", variant="success")
                yield Button("Always allow", id="always", variant="primary")
                yield Button("Deny", id="deny", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        choice = {
            "allow": ApprovalOutcome(allowed=True),
            "always": ApprovalOutcome(allowed=True, remember=True),
            "deny": ApprovalOutcome(allowed=False),
        }
        self.dismiss(choice.get(event.button.id or "deny", ApprovalOutcome(allowed=False)))

    def action_deny(self) -> None:
        self.dismiss(ApprovalOutcome(allowed=False))


class SessionScreen(ModalScreen[SessionInfo | None]):
    """Modal listing saved sessions to resume."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, infos: list[SessionInfo]) -> None:
        super().__init__()
        self._infos = infos

    def compose(self) -> ComposeResult:
        options = [
            Option(
                f"{index}. {info.name or info.id} | {info.message_count} msgs | "
                f"{info.cwd or ''}",
                id=info.path.as_posix(),
            )
            for index, info in enumerate(self._infos, start=1)
        ]
        with Vertical(id="session-box"):
            yield Label("Resume session", id="session-title")
            yield OptionList(*options, id="session-list")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        for info in self._infos:
            if info.path.as_posix() == event.option.id:
                self.dismiss(info)
                return
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TextualApprover:
    def __init__(self, app: TMPromptApp) -> None:
        self._app = app

    async def request(self, action, reason: str) -> ApprovalOutcome:
        return await self._app.push_screen_wait(
            PermissionScreen(action.describe(), reason)
        )


class DeferredApprover:
    """Holds the real approver; lets the agent be built before the app exists."""

    def __init__(self) -> None:
        self._inner = None

    def set(self, approver) -> None:
        self._inner = approver

    async def request(self, action, reason: str) -> ApprovalOutcome:
        if self._inner is None:
            return ApprovalOutcome(allowed=False)
        return await self._inner.request(action, reason)


class TMPromptApp(App[None]):
    CSS = """
    #log { height: 1fr; border: round $accent; }
    #stream { height: auto; max-height: 6; padding: 0 1; }
    #prompt { dock: bottom; }
    #perm-box { width: 60%; height: auto; padding: 1 2; background: $panel; border: round $warning; }
    #perm-title { text-style: bold; padding-bottom: 1; }
    #perm-body { padding-bottom: 1; }
    #perm-buttons { height: auto; align-horizontal: right; }
    #session-box { width: 70%; height: auto; max-height: 70%; padding: 1 2; background: $panel; border: round $accent; }
    #session-title { text-style: bold; padding-bottom: 1; }
    #session-list { height: auto; max-height: 20; }
    """
    BINDINGS = [Binding("ctrl+q", "quit", "Quit")]

    def __init__(self, agent: Agent, model: Model) -> None:
        super().__init__()
        self._agent = agent
        self._model = model
        self.command_handler: Callable[[str], Awaitable[str | None]] | None = None
        self.resume_on_start = False

    def write_line(self, text: str, style: str = "") -> None:
        self.query_one("#log", RichLog).write(Text(text, style=style))

    async def pick_session(self, infos: list[SessionInfo]) -> SessionInfo | None:
        return await self.push_screen_wait(SessionScreen(infos))

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="log", wrap=True, markup=False, highlight=False)
        yield Static("", id="stream")
        yield Input(placeholder="Ask TM to do something, then Enter.", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"TM - {self._model.provider}/{self._model.id}"
        self.sub_title = "idle"
        self._agent.subscribe(self._on_agent_event)
        self.query_one("#prompt", Input).focus()
        self.query_one("#log", RichLog).write(
            Text("TM ready. Type a request, /help for commands, /exit to quit.", style="dim")
        )
        if self.resume_on_start:
            self.run_worker(self._resume_startup(), exclusive=True)

    async def _resume_startup(self) -> None:
        if self.command_handler is not None:
            await self.command_handler("resume")

    async def _on_agent_event(self, event: AgentEvent) -> None:
        log = self.query_one("#log", RichLog)
        stream = self.query_one("#stream", Static)
        if isinstance(event, AgentStartEvent):
            self.sub_title = "working"
        elif isinstance(event, AgentEndEvent):
            self.sub_title = "idle"
        elif isinstance(event, MessageStartEvent) and isinstance(
            event.message, AssistantMessage
        ):
            stream.update("")
        elif isinstance(event, MessageUpdateEvent):
            text = event.message.text()
            if text:
                stream.update(Text(text))
            else:
                thinking = event.message.thinking()
                if thinking:
                    stream.update(Text(thinking, style="dim italic"))
        elif isinstance(event, MessageEndEvent) and isinstance(
            event.message, AssistantMessage
        ):
            text = event.message.text()
            if text:
                log.write(Text(text))
            usage = event.message.usage
            if usage and (usage.input or usage.output):
                log.write(Text(f"tokens: in={usage.input} out={usage.output}", style="dim"))
            stream.update("")
        elif isinstance(event, ToolExecutionStartEvent):
            self.sub_title = f"tool: {event.tool_name}"
            arguments = json.dumps(event.arguments, ensure_ascii=False)
            log.write(Text(f"> {event.tool_name} {arguments}", style="yellow"))
        elif isinstance(event, ToolExecutionEndEvent):
            style = "red" if event.is_error else "dim"
            log.write(Text(event.output, style=style))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        self.query_one("#log", RichLog).write(Text(f"you> {text}", style="bold cyan"))
        if text.startswith("/"):
            if self.command_handler is None:
                return
            try:
                forwarded = await self.command_handler(text[1:])
            except ExitSignal:
                self.exit()
                return
            self.query_one("#prompt", Input).focus()
            if forwarded is None:
                return
            text = forwarded
        self.run_worker(self._prompt(text), exclusive=True)

    async def _prompt(self, text: str) -> None:
        try:
            await self._agent.prompt(text)
        except Exception as exc:  # noqa: BLE001 - surface failures in the UI
            self.query_one("#log", RichLog).write(Text(f"error: {exc}", style="bold red"))
        finally:
            self.query_one("#prompt", Input).focus()


__all__ = ["DeferredApprover", "PermissionScreen", "TMPromptApp", "TextualApprover"]
