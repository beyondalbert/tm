"""Textual terminal UI for TM, styled to match pi's interactive mode."""

from __future__ import annotations

import io
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown as RichMarkdown
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.message import Message
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Button, Label, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from tm.ai.types import AssistantMessage, Model, TextContent, ToolResultMessage, Usage, UserMessage
from tm.cli.autocomplete import (
    Completion,
    FileIndex,
    Span,
    apply_completion,
    command_completions,
    detect,
)
from tm.cli.commands import COMMAND_SPECS, ExitSignal
from tm.clipboard import copy_to_clipboard
from tm.core.agent import Agent
from tm.core.compaction import estimate_tokens
from tm.core.events import (
    AgentEndEvent,
    AgentEvent,
    AgentNoticeEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)
from tm.core.session import SessionInfo
from tm.permissions import ApprovalOutcome, SessionApprover

# pi dark theme palette
_TEXT = "#d4d4d4"
_DIM = "#666666"
_MUTED = "#808080"
_ACCENT = "#8abeb7"
_BORDER_MUTED = "#505050"
_ERROR = "#cc6666"
_WARNING = "#ffff00"
_USER_BG = "#343541"
_INPUT_BG = "#26262e"
_TOOL_BG = "#282832"
_TOOL_OK_BG = "#283228"
_TOOL_ERR_BG = "#3c2828"

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def format_tokens(count: int) -> str:
    if count < 1000:
        return str(count)
    if count < 1_000_000:
        return f"{count / 1000:.1f}k"
    return f"{count / 1_000_000:.1f}M"


def _display_path(value: object, cwd: Path | None = None) -> str:
    raw = str(value) if value else "."
    base = cwd or Path.cwd()
    path = Path(raw)
    if not path.is_absolute():
        path = base / path
    try:
        return str(path.resolve().relative_to(base.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def tool_title(name: str, arguments: dict) -> Text:
    """Render a tool call title the way pi does (name bold, args coloured)."""
    text = Text()
    if name == "read":
        text.append("read", f"bold {_TEXT}")
        text.append(" " + _display_path(arguments.get("path")), _ACCENT)
        offset, limit = arguments.get("offset"), arguments.get("limit")
        if offset is not None or limit is not None:
            start = offset or 1
            end = start + limit - 1 if limit is not None else None
            text.append(f":{start}" + (f"-{end}" if end else ""), _WARNING)
    elif name in ("write", "edit"):
        text.append(name, f"bold {_TEXT}")
        text.append(" " + _display_path(arguments.get("path")), _ACCENT)
    elif name == "shell":
        text.append("$ ", f"bold {_TEXT}")
        text.append(str(arguments.get("command", "")), f"bold {_TEXT}")
    elif name == "grep":
        text.append("grep", f"bold {_TEXT}")
        text.append(" /" + str(arguments.get("pattern", "")) + "/", _ACCENT)
        text.append(" in " + _display_path(arguments.get("path", ".")), _MUTED)
        if arguments.get("glob"):
            text.append(f" ({arguments['glob']})", _MUTED)
    elif name == "find":
        text.append("find", f"bold {_TEXT}")
        text.append(" " + str(arguments.get("pattern", "")), _ACCENT)
        text.append(" in " + _display_path(arguments.get("path", ".")), _MUTED)
    elif name == "ls":
        text.append("ls", f"bold {_TEXT}")
        text.append(" " + _display_path(arguments.get("path", ".")), _ACCENT)
    else:
        text.append(name, f"bold {_TEXT}")
        if arguments:
            text.append(" " + json.dumps(arguments, ensure_ascii=False), _MUTED)
    return text


def render_markdown(markdown: str, width: int) -> Content:
    """Render Markdown to a Rich-styled ``Content``.

    Textual can only select text from ``Text``/``Content`` visuals, so a plain
    ``Markdown`` renderable is not copyable. Rendering it to ``Content`` keeps
    the styling and makes the block selectable with correct offsets.
    """
    width = max(width, 1)
    console = Console(
        width=width,
        file=io.StringIO(),
        force_terminal=True,
        color_system="truecolor",
    )
    text = Text()
    for segment in console.render(RichMarkdown(markdown), console.options.update_width(width)):
        text.append(segment.text, segment.style)
    return Content.from_rich_text(text)


class UserMessageWidget(Static):
    """User text on a full-width background block with vertical padding."""

    def __init__(self, text: str) -> None:
        super().__init__(classes="user")
        self._text = text.strip()

    def on_mount(self) -> None:
        self._render_markdown()

    def on_resize(self) -> None:
        self._render_markdown()

    def _render_markdown(self) -> None:
        if not self._text:
            return
        width = self.size.width or self.app.size.width - 2
        if width > 0:
            self.update(render_markdown(self._text, width))


class AssistantMessageWidget(Vertical):
    """Assistant text with an optional dim/italic thinking block and error line."""

    def __init__(self) -> None:
        super().__init__(classes="assistant")
        self._thinking = Static(Text(""), classes="thinking")
        self._body = Static(Text(""), classes="body")
        self._error = Static(Text(""), classes="error")
        self._text = ""
        for widget in (self._thinking, self._body, self._error):
            widget.display = False

    def compose(self) -> ComposeResult:
        yield self._thinking
        yield self._body
        yield self._error

    def set_content(self, thinking: str, text: str, *, error: str | None = None) -> None:
        if thinking.strip():
            self._thinking.update(Text(thinking.strip(), style=f"italic {_MUTED}"))
            self._thinking.display = True
        else:
            self._thinking.display = False

        self._text = text.strip()
        if self._text:
            self._render_body()
            self._body.display = True
        else:
            self._body.display = False

        if error:
            self._error.update(Text(error, style=_ERROR))
            self._error.display = True
        else:
            self._error.display = False

    def on_resize(self) -> None:
        self._render_body()

    def _render_body(self) -> None:
        if not self._text:
            return
        width = self._body.size.width or self.size.width or self.app.size.width - 2
        if width > 0:
            self._body.update(render_markdown(self._text, width))


class ToolWidget(Vertical):
    """A tool call, folded to its title line.

    Collapsed by default so the conversation stays readable; ``ctrl+o`` expands
    or collapses every tool call. A collapsed error still shows its first line.
    """

    def __init__(self, name: str, arguments: dict, *, expanded: bool = False) -> None:
        super().__init__(classes="tool")
        self._base_title = tool_title(name, arguments)
        self._title = Static(self._base_title, classes="tool-title")
        self._body = Static(Text(""), classes="tool-body")
        self._body.display = False
        self._output = ""
        self._is_error = False
        self._expanded = expanded
        self._finished = False

    def compose(self) -> ComposeResult:
        yield self._title
        yield self._body

    def on_mount(self) -> None:
        self._render_title()

    def set_result(self, output: str, is_error: bool) -> None:
        self._output = output.rstrip()
        self._is_error = is_error
        self._finished = True
        self.set_class(True, "error" if is_error else "success")
        self._render_title()
        self._render_body()

    def set_expanded(self, expanded: bool) -> None:
        if expanded != self._expanded:
            self._expanded = expanded
            self._render_title()
            self._render_body()

    def _render_title(self) -> None:
        text = Text()
        text.append("\u25be " if self._expanded else "\u25b8 ", style=_MUTED)
        text.append_text(self._base_title)
        if self._finished:
            lines = len(self._output.splitlines()) if self._output else 0
            if self._is_error:
                text.append("  [error]", style=_ERROR)
            if lines:
                hint = "(ctrl+o to collapse)" if self._expanded else f"({lines} lines, ctrl+o)"
                text.append(f"  {hint}", style=_MUTED)
            elif not self._is_error:
                text.append("  (no output)", style=_MUTED)
        self._title.update(text)

    def _render_body(self) -> None:
        if not self._output:
            self._body.display = False
            return
        if self._expanded:
            self._body.update(Text(self._output, style=_MUTED))
            self._body.display = True
            return
        if self._is_error:
            # Keep failures visible even while folded.
            self._body.update(Text(self._output.splitlines()[0], style=_ERROR))
            self._body.display = True
            return
        self._body.display = False


class SystemNote(Static):
    def __init__(self, text: str, style: str = _DIM) -> None:
        super().__init__(Text(text, style=style), classes="system")


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


def _cursor_offset(area: TextArea) -> int:
    """The cursor's character offset in a TextArea's document."""
    row, column = area.cursor_location
    lines = area.document.lines
    return sum(len(line) + 1 for line in lines[:row]) + column


class PromptArea(TextArea):
    """Multi-line prompt editor.

    Pasting keeps every line (unlike Textual's single-line ``Input``). Enter
    submits; Shift+Enter or Ctrl+J insert a newline. While the completion list
    is open, Tab/Up/Down/Escape/Enter drive it as before.
    """

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    async def _on_key(self, event: events.Key) -> None:
        app = self.app
        if (
            event.key == "ctrl+c"
            and isinstance(app, TMPromptApp)
            and app.is_working
        ):
            event.stop()
            event.prevent_default()
            app.action_abort_run()
            return
        if isinstance(app, TMPromptApp) and app.suggestions_active:
            if event.key == "tab":
                event.stop()
                event.prevent_default()
                app.accept_suggestion()
                return
            if event.key == "enter" and app.accepts_enter():
                event.stop()
                event.prevent_default()
                app.accept_suggestion()
                return
            if event.key == "down":
                event.stop()
                event.prevent_default()
                app.move_suggestion(1)
                return
            if event.key == "up":
                event.stop()
                event.prevent_default()
                app.move_suggestion(-1)
                return
            if event.key == "escape":
                event.stop()
                event.prevent_default()
                app.close_suggestions()
                return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self.text))
            return
        if event.key in ("shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)


class TMPromptApp(App[None]):
    CSS = f"""
    .user {{ background: {_USER_BG}; color: {_TEXT}; width: 1fr; padding: 1 1; margin-bottom: 1; }}
    .assistant {{ width: 1fr; height: auto; padding: 0 1; margin-bottom: 1; }}
    .assistant .thinking {{ color: {_MUTED}; text-style: italic; width: 1fr; }}
    .assistant .body {{ color: {_TEXT}; width: 1fr; }}
    .assistant .error {{ color: {_ERROR}; width: 1fr; }}
    .tool {{ width: 1fr; height: auto; padding: 0 1; margin-bottom: 1; }}
    .tool .tool-title {{ width: 1fr; }}
    .tool .tool-body {{ width: 1fr; background: {_TOOL_BG}; padding: 0 1; }}
    .tool.success .tool-body {{ background: {_TOOL_OK_BG}; }}
    .tool.error .tool-body {{ background: {_TOOL_ERR_BG}; }}
    .system {{ color: {_DIM}; width: 1fr; margin-bottom: 1; }}
    #messages {{ height: 1fr; }}
    #editor-status {{ height: 1; }}
    #suggestions {{ display: none; height: auto; max-height: 8; border: round {_ACCENT}; }}
    #prompt {{ height: auto; max-height: 8; border: none; background: {_INPUT_BG}; padding: 0 1; }}
    #footer {{ height: 2; padding: 0 1; }}
    #perm-box {{ width: 60%; height: auto; padding: 1 2; background: $panel; border: round {_WARNING}; }}
    #perm-title {{ text-style: bold; padding-bottom: 1; }}
    #perm-body {{ padding-bottom: 1; }}
    #perm-buttons {{ height: auto; align-horizontal: right; }}
    #session-box {{ width: 70%; height: auto; max-height: 70%; padding: 1 2; background: $panel; border: round {_ACCENT}; }}
    #session-title {{ text-style: bold; padding-bottom: 1; }}
    #session-list {{ height: auto; max-height: 20; }}
    """
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+o", "toggle_tools", "Expand tools"),
        Binding("ctrl+y", "toggle_auto", "Auto-approve"),
        Binding("ctrl+p", "toggle_pause", "Pause"),
        Binding("ctrl+shift+c", "copy_selection", "Copy selection", show=False),
        Binding("tab", "accept_suggestion", "Complete", show=False),
        Binding("down", "suggestion_down", "Next suggestion", show=False),
        Binding("up", "suggestion_up", "Previous suggestion", show=False),
        Binding("escape", "dismiss_suggestions", "Dismiss suggestions", show=False),
    ]

    def __init__(self, agent: Agent, model: Model, *, banner: str | None = None) -> None:
        super().__init__()
        self._agent = agent
        self._model = model
        self._banner = banner
        self.command_handler: Callable[[str], Awaitable[str | None]] | None = None
        self.resume_on_start = False
        self.recover_on_start = False
        self.session_approver: SessionApprover | None = None
        self._status = "idle"
        self._current: AssistantMessageWidget | None = None
        self._current_mounted = False
        self._tools: dict[str, ToolWidget] = {}
        self._expanded = False
        self._spin_index = 0
        self._spinner_timer: Timer | None = None
        self._suggestions: list[Completion] = []
        self._suggestion_span: Span | None = None
        self._file_index: FileIndex | None = None

    # -- small public API used by commands / tests ------------------------
    def write_line(self, text: str, style: str = _DIM) -> None:
        self.run_worker(self._mount(SystemNote(text, style)), exit_on_error=False)

    async def pick_session(self, infos: list[SessionInfo]) -> SessionInfo | None:
        return await self.push_screen_wait(SessionScreen(infos))

    # -- layout -----------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="messages")
        yield Static("", id="editor-status")
        yield OptionList(id="suggestions")
        yield PromptArea(
            placeholder="Ask TM to do something, then Enter. Shift+Enter for a new line.",
            id="prompt",
        )
        yield Static("", id="footer")

    def on_mount(self) -> None:
        self._agent.subscribe(self._on_agent_event)
        self._update_editor_status()
        self._update_footer()
        self.query_one("#prompt", TextArea).focus()
        self.run_worker(self._startup(), exclusive=True, exit_on_error=False)

    async def _startup(self) -> None:
        if self._banner:
            await self._mount(SystemNote(self._banner))
        if self.resume_on_start and self.command_handler is not None:
            await self.command_handler("resume")
        if self._agent.messages:
            await self._render_history()
        if self.recover_on_start and await self._agent.recover():
            self.write_line("recovered an interrupted operation", _DIM)

    @staticmethod
    def _user_text(message: UserMessage) -> str:
        if isinstance(message.content, str):
            return message.content
        return " ".join(b.text for b in message.content if isinstance(b, TextContent))

    async def _render_history(self) -> None:
        """Render the messages already loaded from a resumed session."""
        widgets: list[Widget] = []
        tools: dict[str, ToolWidget] = {}
        for message in self._agent.messages:
            if isinstance(message, UserMessage):
                widgets.append(UserMessageWidget(self._user_text(message)))
            elif isinstance(message, AssistantMessage):
                if message.text().strip() or message.thinking().strip() or message.stop_reason == "error":
                    widget = AssistantMessageWidget()
                    error = (
                        f"Error: {message.error_message or 'unknown error'}"
                        if message.stop_reason == "error"
                        else None
                    )
                    widget.set_content(message.thinking(), message.text(), error=error)
                    widgets.append(widget)
                for call in message.tool_calls:
                    tool_widget = ToolWidget(
                        call.name, call.arguments, expanded=self._expanded
                    )
                    tools[call.id] = tool_widget
                    widgets.append(tool_widget)
            elif isinstance(message, ToolResultMessage):
                existing = tools.get(message.tool_call_id)
                if existing is not None:
                    existing.set_result(message.text(), message.is_error)
                else:
                    widgets.append(SystemNote(f"[{message.tool_name}] {message.text()}"))
        if widgets:
            container = self.query_one("#messages", VerticalScroll)
            await container.mount(*widgets)
            container.scroll_end(animate=False)

    async def _reload_history(self) -> None:
        container = self.query_one("#messages", VerticalScroll)
        await container.remove_children()
        await self._render_history()

    async def _mount(self, widget: Widget) -> None:
        container = self.query_one("#messages", VerticalScroll)
        await container.mount(widget)
        container.scroll_end(animate=False)

    def action_toggle_tools(self) -> None:
        self._expanded = not self._expanded
        for widget in self.query(ToolWidget):
            widget.set_expanded(self._expanded)

    def action_toggle_auto(self) -> None:
        approver = self.session_approver
        if approver is None:
            return
        approver.auto = not approver.auto
        self.notify(f"auto-approve {'on' if approver.auto else 'off'}")
        self._update_footer()

    def action_toggle_pause(self) -> None:
        if self._status == "idle":
            return
        if self._agent.pause.paused:
            self._agent.pause.resume()
            self._set_status("working")
            self.notify("resumed")
        else:
            self._agent.pause.pause()
            self._set_status("paused")
            self.notify("paused")

    def action_copy_selection(self) -> None:
        """Copy the mouse selection, or the last reply, to the system clipboard."""
        selection = self.screen.get_selected_text()
        if selection:
            self._copy(selection, "selection")
            return
        reply = self._last_reply()
        if reply:
            self._copy(reply, "last reply")
            return
        self.notify("nothing to copy")

    def _copy(self, text: str, label: str) -> None:
        if copy_to_clipboard(text):
            self.notify(f"copied {label}")
            return
        # Fall back to OSC 52 for terminals that support it.
        self.copy_to_clipboard(text)
        self.notify(f"copied {label} (OSC52)")

    def _last_reply(self) -> str:
        for message in reversed(self._agent.messages):
            if isinstance(message, AssistantMessage):
                text = message.text()
                if text:
                    return text
        return ""

    # -- autocomplete -----------------------------------------------------
    @property
    def suggestions_active(self) -> bool:
        return bool(self._suggestions)

    def accepts_enter(self) -> bool:
        """Enter completes only when the suggestion extends the typed token.

        Pressing Enter on an already-complete token (e.g. ``/new``) submits it
        instead of replacing it.
        """
        if not self._suggestions or self._suggestion_span is None:
            return False
        options = self.query_one("#suggestions", OptionList)
        index = options.highlighted if options.highlighted is not None else 0
        if not 0 <= index < len(self._suggestions):
            return False
        value = self._suggestions[index].value
        token = self._suggestion_span.token
        return value.startswith(token) and value != token

    def _file_index_for_cwd(self) -> FileIndex:
        if self._file_index is None:
            self._file_index = FileIndex(Path.cwd())
        return self._file_index

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self._refresh_suggestions()

    def _refresh_suggestions(self) -> None:
        inp = self.query_one("#prompt", TextArea)
        span = detect(inp.text, _cursor_offset(inp))
        if span is None:
            self.close_suggestions()
            return
        if span.kind == "command":
            completions = command_completions(span.token, COMMAND_SPECS)
        else:
            completions = [
                Completion(f"@{c.value}", c.label, c.description)
                for c in self._file_index_for_cwd().match(span.token[1:])
            ]
        self._suggestion_span = span
        self._show_suggestions(completions)

    def _show_suggestions(self, completions: list[Completion]) -> None:
        options = self.query_one("#suggestions", OptionList)
        options.clear_options()
        self._suggestions = completions
        if not completions:
            options.display = False
            return
        options.add_options(
            [
                Option(f"{c.label}  {c.description}".rstrip(), id=str(index))
                for index, c in enumerate(completions)
            ]
        )
        options.highlighted = 0
        options.display = True

    def close_suggestions(self) -> None:
        self._suggestions = []
        self._suggestion_span = None
        self.query_one("#suggestions", OptionList).display = False

    def move_suggestion(self, delta: int) -> None:
        if not self._suggestions:
            return
        options = self.query_one("#suggestions", OptionList)
        current = options.highlighted if options.highlighted is not None else 0
        options.highlighted = (current + delta) % len(self._suggestions)

    def accept_suggestion(self) -> None:
        if not self._suggestions or self._suggestion_span is None:
            return
        options = self.query_one("#suggestions", OptionList)
        index = options.highlighted if options.highlighted is not None else 0
        if not 0 <= index < len(self._suggestions):
            return
        inp = self.query_one("#prompt", TextArea)
        new_value = apply_completion(
            inp.text, self._suggestion_span, self._suggestions[index].value
        )
        inp.text = new_value
        inp.move_cursor(inp.document.end)
        self.close_suggestions()
        inp.focus()

    def action_accept_suggestion(self) -> None:
        self.accept_suggestion()

    def action_suggestion_down(self) -> None:
        self.move_suggestion(1)

    def action_suggestion_up(self) -> None:
        self.move_suggestion(-1)

    def action_dismiss_suggestions(self) -> None:
        if self.suggestions_active:
            self.close_suggestions()
            return
        self.action_abort_run()

    @property
    def is_working(self) -> bool:
        return self._status != "idle"

    def action_abort_run(self) -> None:
        """Stop the current turn (Esc, or Ctrl+C in the prompt)."""
        if not self.is_working:
            return
        self._agent.abort()
        self.notify("stopping…")

    # -- status / editor line --------------------------------------------
    def _set_status(self, status: str) -> None:
        self._status = status
        self.sub_title = status
        if status in ("idle", "paused"):
            if self._spinner_timer is not None:
                self._spinner_timer.stop()
                self._spinner_timer = None
        elif self._spinner_timer is None:
            self._spinner_timer = self.set_interval(0.1, self._tick_spinner)
        self._update_editor_status()

    def _tick_spinner(self) -> None:
        self._spin_index = (self._spin_index + 1) % len(_SPINNER)
        self._update_editor_status()

    def _update_editor_status(self) -> None:
        width = max(self.size.width, 20)
        line = Text()
        if self._status == "idle":
            line.append("─" * width, style=_BORDER_MUTED)
        elif self._status == "paused":
            label = "Paused  Ctrl+P to resume"
            line.append("── ", style=_BORDER_MUTED)
            line.append(label, style=_WARNING)
            line.append(" " + "─" * max(0, width - len(label) - 3), style=_BORDER_MUTED)
        else:
            label = f"{_SPINNER[self._spin_index]} Working"
            hint = "  Esc/Ctrl+C stop  ·  Ctrl+P pause"
            head = f"── {label} "
            tail = "─" * max(0, width - len(head) - len(hint))
            line.append(head, style=_BORDER_MUTED)
            line.append(label, style=_TEXT)
            line.append(tail, style=_BORDER_MUTED)
            line.append(hint, style=_DIM)
        self.query_one("#editor-status", Static).update(line)

    def _git_branch(self) -> str | None:
        head = Path.cwd() / ".git" / "HEAD"
        try:
            text = head.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if text.startswith("ref: refs/heads/"):
            return text[len("ref: refs/heads/") :]
        return None

    def _update_footer(self) -> None:
        model = self._agent.model
        cwd = str(Path.cwd())
        home = str(Path.home())
        if cwd.startswith(home):
            cwd = "~" + cwd[len(home) :]
        branch = self._git_branch()
        if branch:
            cwd = f"{cwd} ({branch})"

        total_in = total_out = cache_read = 0
        if self._agent.session is not None:
            totals = self._agent.session.usage_totals()
            total_in, total_out, cache_read = totals.input, totals.output, totals.cache_read
        else:
            for message in self._agent.messages:
                if isinstance(message, AssistantMessage) and message.usage:
                    total_in += message.usage.input
                    total_out += message.usage.output
                    cache_read += message.usage.cache_read
        used = estimate_tokens(self._agent.messages, self._agent.system_prompt)
        window = model.context_window or 0

        stats = f"↑{format_tokens(total_in)} ↓{format_tokens(total_out)}"
        if cache_read:
            stats += f" R{format_tokens(cache_read)}"
            prompt_tokens = total_in + cache_read
            if prompt_tokens:
                stats += f" CH{cache_read * 100 // prompt_tokens}%"
        cost = model.cost(
            Usage(
                input=total_in,
                output=total_out,
                cache_read=cache_read,
                total=total_in + total_out + cache_read,
            )
        )
        if cost:
            stats += f"  ${cost:.4f}"
        if window:
            stats += f"  {used * 100 // window}%/{format_tokens(window)} (auto)"

        right = f"({model.provider}) {model.id}"
        if model.reasoning:
            right += " • thinking"
        if self.session_approver is not None and self.session_approver.auto:
            right += " • AUTO"
        width = max(self.size.width - 2, 20)
        pad = max(1, width - len(stats) - len(right))
        self.query_one("#footer", Static).update(
            Text(f"{cwd}\n{stats}{' ' * pad}{right}", style=_DIM)
        )

    # -- agent events -----------------------------------------------------
    async def _on_agent_event(self, event: AgentEvent) -> None:
        if isinstance(event, AgentStartEvent):
            self._set_status("working")
        elif isinstance(event, AgentEndEvent):
            self._set_status("idle")
            self._update_footer()
            if event.stop_reason == "max_turns":
                await self._mount(
                    SystemNote(
                        f"Stopped after {self._agent.max_turns} turns "
                        "(raise it with --max-turns). Send another message to continue.",
                        _WARNING,
                    )
                )
            elif event.stop_reason == "aborted":
                await self._mount(SystemNote("Stopped by user.", _WARNING))
        elif isinstance(event, AgentNoticeEvent):
            style = _WARNING if event.level in ("warning", "error") else _DIM
            await self._mount(SystemNote(event.text, style))
        elif isinstance(event, MessageStartEvent) and isinstance(
            event.message, AssistantMessage
        ):
            self._current = AssistantMessageWidget()
            self._current_mounted = False
        elif isinstance(event, MessageUpdateEvent):
            if self._current is not None:
                thinking = event.message.thinking()
                text = event.message.text()
                if (thinking.strip() or text.strip()) and not self._current_mounted:
                    await self._mount(self._current)
                    self._current_mounted = True
                if self._current_mounted:
                    self._current.set_content(thinking, text)
                    self.query_one("#messages", VerticalScroll).scroll_end(animate=False)
        elif isinstance(event, MessageEndEvent) and isinstance(
            event.message, AssistantMessage
        ):
            message = event.message
            error = None
            if message.stop_reason == "error":
                error = f"Error: {message.error_message or 'unknown error'}"
            thinking = message.thinking()
            text = message.text()
            # A provider may fail before emitting a start event, in which case no
            # widget was created yet (e.g. authentication errors).
            if self._current is None and (thinking.strip() or text.strip() or error):
                self._current = AssistantMessageWidget()
                self._current_mounted = False
            if self._current is not None:
                if thinking.strip() or text.strip() or error:
                    if not self._current_mounted:
                        await self._mount(self._current)
                        self._current_mounted = True
                    self._current.set_content(thinking, text, error=error)
                self._current = None
                self._current_mounted = False
            self._update_footer()
        elif isinstance(event, ToolExecutionStartEvent):
            self._set_status("working")
            widget = ToolWidget(event.tool_name, event.arguments, expanded=self._expanded)
            self._tools[event.tool_call_id] = widget
            await self._mount(widget)
        elif isinstance(event, ToolExecutionEndEvent):
            finished = self._tools.get(event.tool_call_id)
            if finished is not None:
                del self._tools[event.tool_call_id]
                finished.set_result(event.output, event.is_error)
            self.query_one("#messages", VerticalScroll).scroll_end(animate=False)

    # -- input ------------------------------------------------------------
    def on_prompt_area_submitted(self, event: PromptArea.Submitted) -> None:
        text = event.value.strip()
        self.query_one("#prompt", TextArea).text = ""
        if not text:
            return
        self.close_suggestions()
        # Run in a worker so commands can push screens (e.g. the session picker).
        self.run_worker(self._handle_input(text), exclusive=True, exit_on_error=False)

    async def _handle_input(self, text: str) -> None:
        await self._mount(UserMessageWidget(text))
        if text.startswith("/") and "\n" not in text:
            if self.command_handler is None:
                return
            command = text[1:].split(None, 1)[0].lower()
            try:
                forwarded = await self.command_handler(text[1:])
            except ExitSignal:
                self.exit()
                return
            self.query_one("#prompt", TextArea).focus()
            self._update_footer()
            if command in ("resume", "new", "tree", "fork"):
                await self._reload_history()
            if forwarded is None:
                return
            text = forwarded
        await self._prompt(text)

    async def _prompt(self, text: str) -> None:
        # Show activity immediately: auto-compaction may run before the first token.
        self._set_status("working")
        try:
            await self._agent.prompt(text)
        except Exception as exc:  # noqa: BLE001 - surface failures in the UI
            await self._mount(SystemNote(f"error: {exc}", _ERROR))
        finally:
            self._set_status("idle")
            self._update_footer()
            self.query_one("#prompt", TextArea).focus()


__all__ = [
    "DeferredApprover",
    "PermissionScreen",
    "SessionScreen",
    "TMPromptApp",
    "TextualApprover",
]
