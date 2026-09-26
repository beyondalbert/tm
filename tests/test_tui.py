from __future__ import annotations

import pytest

pytest.importorskip("textual")

from pydantic import BaseModel  # noqa: E402
from textual.app import App  # noqa: E402
from textual.widgets import TextArea  # noqa: E402

from tm.ai.event_stream import EventStream  # noqa: E402
from tm.ai.types import (  # noqa: E402
    AssistantMessage,
    DoneEvent,
    ErrorEvent,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    ToolCall,
    UserMessage,
)
from tm.core.agent import Agent  # noqa: E402
from tm.core.session import SessionInfo  # noqa: E402
from tm.permissions import ApprovalOutcome  # noqa: E402
from tm.tools.base import Tool, ToolContext, ToolResult, text_result  # noqa: E402
from tm.tui.app import PermissionScreen, SessionScreen, TMPromptApp  # noqa: E402

FAKE_MODEL = Model(id="fake", provider="fake")


class EchoParams(BaseModel):
    text: str


class EchoTool(Tool[EchoParams]):
    name = "echo"
    description = "Echo text."
    parameters_model = EchoParams

    async def execute(self, call_id: str, args: EchoParams, ctx: ToolContext) -> ToolResult:
        return text_result(args.text)


class LongParams(BaseModel):
    pass


class LongTool(Tool[LongParams]):
    name = "long"
    description = "Return many lines."
    parameters_model = LongParams

    async def execute(self, call_id: str, args: LongParams, ctx: ToolContext) -> ToolResult:
        return text_result("\n".join(f"line {i}" for i in range(40)))


class FailTool(Tool[LongParams]):
    name = "fail"
    description = "Return an error."
    parameters_model = LongParams

    async def execute(self, call_id: str, args: LongParams, ctx: ToolContext) -> ToolResult:
        return text_result("first error line\nsecond error line", is_error=True)


def fake_stream_fn(model, context, options) -> EventStream:
    message = AssistantMessage(content=[TextContent(text="hello from fake")], stop_reason="stop")
    stream: EventStream = EventStream()
    stream.push(StartEvent(partial=message))
    stream.push(TextDeltaEvent(partial=message, delta=message.text()))
    stream.push(DoneEvent(partial=message, message=message))
    stream.end(message)
    return stream


async def test_permission_screen_allow() -> None:
    results: list[ApprovalOutcome | None] = []

    class Host(App):
        def on_mount(self) -> None:
            self.push_screen(PermissionScreen("shell: ls", "default policy"), results.append)

    app = Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#allow")
        await pilot.pause()

    assert results and results[0] is not None
    assert results[0].allowed is True
    assert results[0].remember is False


async def test_permission_screen_always_and_deny() -> None:
    results: list[ApprovalOutcome | None] = []

    class Host(App):
        def on_mount(self) -> None:
            self.push_screen(PermissionScreen("shell: rm", "deny rule"), results.append)

    app = Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#always")
        await pilot.pause()
    assert results[0] is not None and results[0].remember is True

    results.clear()
    app = Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#deny")
        await pilot.pause()
    assert results[0] is not None and results[0].allowed is False


async def test_tm_app_runs_a_prompt() -> None:
    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn)
    app = TMPromptApp(agent, FAKE_MODEL)
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.text = "do something"
        prompt.move_cursor(prompt.document.end)
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

    assert len(agent.messages) >= 1
    assert any(
        isinstance(message, AssistantMessage) and message.text() == "hello from fake"
        for message in agent.messages
    )


async def test_tui_slash_command_forwards_expanded_prompt() -> None:
    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn)
    app = TMPromptApp(agent, FAKE_MODEL)

    async def handler(text: str) -> str | None:
        if text == "greet":
            return "expanded greeting prompt"
        return None

    app.command_handler = handler
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.text = "/greet"
        prompt.move_cursor(prompt.document.end)
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

    assert any(
        isinstance(message, UserMessage) and message.content == "expanded greeting prompt"
        for message in agent.messages
    )


async def test_session_screen_selects_first(tmp_path) -> None:
    infos = [
        SessionInfo(
            id="abc123",
            path=tmp_path / "s.jsonl",
            name="mine",
            cwd=str(tmp_path),
            created=1,
            message_count=3,
            updated=2,
            preview="hi",
        )
    ]
    results: list[SessionInfo | None] = []

    class Host(App):
        def on_mount(self) -> None:
            self.push_screen(SessionScreen(infos), results.append)

    app = Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

    assert results and results[0] is infos[0]


async def test_session_screen_escape_cancels(tmp_path) -> None:
    infos = [
        SessionInfo(id="x", path=tmp_path / "s.jsonl", name="n", cwd=str(tmp_path), created=1)
    ]
    results: list[SessionInfo | None] = []

    class Host(App):
        def on_mount(self) -> None:
            self.push_screen(SessionScreen(infos), results.append)

    app = Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

    assert results == [None]


def scripted(messages: list[AssistantMessage]):
    iterator = iter(messages)

    def stream_fn(model, context, options) -> EventStream:
        message = next(iterator)
        stream: EventStream = EventStream()
        stream.push(StartEvent(partial=message))
        if message.text():
            stream.push(TextDeltaEvent(partial=message, delta=message.text()))
        stream.push(DoneEvent(partial=message, message=message))
        stream.end(message)
        return stream

    return stream_fn


def failing_stream_fn(model, context, options) -> EventStream:
    """Provider that fails before any start event (e.g. a 401)."""

    message = AssistantMessage(model="fake", stop_reason="error", error_message="boom")
    stream: EventStream = EventStream()
    stream.push(ErrorEvent(error="boom", message=message, partial=message))
    stream.end(message)
    return stream


async def test_tui_does_not_render_empty_assistant_block() -> None:
    tool_turn = AssistantMessage(
        tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "hi"})],
        stop_reason="tool_use",
    )
    final = AssistantMessage(content=[TextContent(text="done")], stop_reason="stop")
    agent = Agent(FAKE_MODEL, stream_fn=scripted([tool_turn, final]), tools=[EchoTool()])
    app = TMPromptApp(agent, FAKE_MODEL)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.text = "go"
        prompt.move_cursor(prompt.document.end)
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        # one user message, one tool block, one assistant block (the tool-only turn adds none)
        assert len(app.query(".user")) == 1
        assert len(app.query(".tool")) == 1
        assert len(app.query(".assistant")) == 1


async def test_tui_renders_provider_error_without_start_event() -> None:
    agent = Agent(FAKE_MODEL, stream_fn=failing_stream_fn)
    app = TMPromptApp(agent, FAKE_MODEL)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.text = "go"
        prompt.move_cursor(prompt.document.end)
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        errors = app.query(".assistant .error")
        assert len(errors) == 1
        assert "Error: boom" in str(errors.first().render())


def test_tui_has_copy_binding() -> None:
    from textual.binding import Binding

    keys = {
        binding.key if isinstance(binding, Binding) else binding[0]
        for binding in TMPromptApp.BINDINGS
    }
    assert "ctrl+shift+c" in keys


async def test_copy_selection_action_is_safe_without_selection() -> None:
    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn)
    app = TMPromptApp(agent, FAKE_MODEL)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_copy_selection()  # no selection: must not raise


async def test_markdown_content_is_selectable() -> None:
    from textual.widgets import Static

    from tm.tui.app import render_markdown

    class Harness(App):
        def compose(self):
            yield Static(render_markdown("hello **world**", 80), id="md")

    harness = Harness()
    async with harness.run_test() as pilot:
        await pilot.pause()
        widget = harness.query_one("#md")
        await pilot.mouse_down(widget, offset=(0, 0))
        await pilot.hover(widget, offset=(5, 0))
        await pilot.mouse_up(widget, offset=(5, 0))
        await pilot.pause()
        selected = harness.screen.get_selected_text()
        assert selected
        assert selected.strip().startswith("hello")


async def test_assistant_reply_body_is_selectable() -> None:
    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn)
    app = TMPromptApp(agent, FAKE_MODEL)
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.text = "hi"
        prompt.move_cursor(prompt.document.end)
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        body = app.query_one(".assistant .body")
        await pilot.mouse_down(body, offset=(0, 0))
        await pilot.hover(body, offset=(5, 0))
        await pilot.mouse_up(body, offset=(5, 0))
        await pilot.pause()
        selected = app.screen.get_selected_text()
        assert selected
        assert selected.strip().startswith("hello")


def test_tool_title_formats() -> None:
    from tm.tui.app import tool_title

    assert str(tool_title("read", {"path": "src/app.py"})) == "read src/app.py"
    assert str(tool_title("read", {"path": "a.py", "offset": 10, "limit": 5})) == "read a.py:10-14"
    assert str(tool_title("shell", {"command": "pytest -q"})) == "$ pytest -q"
    assert "grep /TODO/ in src" in str(tool_title("grep", {"pattern": "TODO", "path": "src"}))
    assert str(tool_title("ls", {"path": "src"})) == "ls src"


async def test_footer_reflects_live_agent_model() -> None:
    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn)
    app = TMPromptApp(agent, FAKE_MODEL)

    async with app.run_test() as pilot:
        await pilot.pause()
        agent.model = Model(id="qwen-max", provider="qwen")
        app._update_footer()
        await pilot.pause()
        assert "qwen-max" in str(app.query_one("#footer").render())


async def test_footer_shows_cost_and_cache(tmp_path) -> None:
    from tm.ai.types import Usage
    from tm.core.session import SessionManager

    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path)
    session.add_usage(Usage(input=1000, output=200, cache_read=800, total=2000))
    model = Model(id="m", provider="p", input_cost=1.0, output_cost=2.0, cache_read_cost=0.1)
    agent = Agent(model, stream_fn=fake_stream_fn, session=session)
    app = TMPromptApp(agent, model)

    async with app.run_test() as pilot:
        await pilot.pause()
        text = str(app.query_one("#footer").render())
        assert "CH" in text
        assert "$" in text


async def test_footer_refreshes_after_model_command() -> None:
    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn)
    app = TMPromptApp(agent, FAKE_MODEL)

    async def handler(text: str) -> str | None:
        if text.startswith("model "):
            agent.model = Model(id="glm-4-plus", provider="zhipu")
        return None

    app.command_handler = handler
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.text = "/model glm-4-plus"
        prompt.move_cursor(prompt.document.end)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert "glm-4-plus" in str(app.query_one("#footer").render())


async def test_tool_output_is_folded_and_ctrl_o_expands() -> None:
    tool_turn = AssistantMessage(
        tool_calls=[ToolCall(id="t1", name="long", arguments={})],
        stop_reason="tool_use",
    )
    final = AssistantMessage(content=[TextContent(text="ok")], stop_reason="stop")
    agent = Agent(FAKE_MODEL, stream_fn=scripted([tool_turn, final]), tools=[LongTool()])
    app = TMPromptApp(agent, FAKE_MODEL)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.text = "go"
        prompt.move_cursor(prompt.document.end)
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        title = str(app.query_one(".tool .tool-title").render())
        assert "\u25b8" in title  # folded marker
        assert "40 lines, ctrl+o" in title
        body = app.query_one(".tool .tool-body")
        assert body.display is False

        app.action_toggle_tools()
        await pilot.pause()
        title = str(app.query_one(".tool .tool-title").render())
        assert "\u25be" in title
        assert "ctrl+o to collapse" in title
        body = app.query_one(".tool .tool-body")
        assert body.display is True
        assert "line 39" in str(body.render())


async def test_folded_error_still_shows_its_first_line() -> None:
    tool_turn = AssistantMessage(
        tool_calls=[ToolCall(id="t1", name="fail", arguments={})],
        stop_reason="tool_use",
    )
    final = AssistantMessage(content=[TextContent(text="ok")], stop_reason="stop")
    agent = Agent(FAKE_MODEL, stream_fn=scripted([tool_turn, final]), tools=[FailTool()])
    app = TMPromptApp(agent, FAKE_MODEL)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.text = "go"
        prompt.move_cursor(prompt.document.end)
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        title = str(app.query_one(".tool .tool-title").render())
        assert "[error]" in title
        body = app.query_one(".tool .tool-body")
        assert body.display is True
        assert "first error line" in str(body.render())
        assert "second error line" not in str(body.render())


async def test_tui_renders_resumed_history(tmp_path) -> None:
    from tm.core.session import SessionManager

    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path)
    session.append(UserMessage(content="earlier question"))
    session.append(AssistantMessage(content=[TextContent(text="earlier answer")], stop_reason="stop"))

    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn, session=session)
    app = TMPromptApp(agent, FAKE_MODEL)

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert len(app.query(".user")) == 1
        assert len(app.query(".assistant")) == 1
        assert app.query(".assistant .body")


async def test_new_command_clears_rendered_history(tmp_path) -> None:
    from tm.core.session import SessionManager

    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path)
    session.append(UserMessage(content="old message"))
    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn, session=session)
    app = TMPromptApp(agent, FAKE_MODEL)

    async def handler(text: str) -> str | None:
        if text == "new":
            agent.reset()
        return None

    app.command_handler = handler
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        assert len(app.query(".user")) == 1

        prompt = app.query_one("#prompt", TextArea)
        prompt.text = "/new"
        prompt.move_cursor(prompt.document.end)
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert len(app.query(".user")) == 0


async def test_tui_command_autocomplete(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn)
    app = TMPromptApp(agent, FAKE_MODEL)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.text = "/mod"
        prompt.move_cursor(prompt.document.end)
        await pilot.pause()
        assert app.query_one("#suggestions").display is True

        await pilot.press("tab")
        await pilot.pause()
        assert prompt.text.startswith("/model")


async def test_tui_file_autocomplete(tmp_path, monkeypatch) -> None:
    (tmp_path / "hello.txt").write_text("x")
    monkeypatch.chdir(tmp_path)
    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn)
    app = TMPromptApp(agent, FAKE_MODEL)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.text = "@hel"
        prompt.move_cursor(prompt.document.end)
        await pilot.pause()
        assert app.query_one("#suggestions").display is True

        await pilot.press("tab")
        await pilot.pause()
        assert prompt.text.startswith("@hello.txt")


async def test_tui_enter_accepts_suggestion(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn)
    app = TMPromptApp(agent, FAKE_MODEL)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.text = "/mod"
        prompt.move_cursor(prompt.document.end)
        await pilot.pause()
        assert app.query_one("#suggestions").display is True

        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert prompt.text.startswith("/model")
        # accepted, not submitted: the agent never ran a prompt
        assert agent.messages == []


async def test_prompt_paste_keeps_every_line() -> None:
    from textual import events

    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn)
    app = TMPromptApp(agent, FAKE_MODEL)
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.focus()
        # Exercise the paste handler directly (Textual's Input would keep only
        # the first line here).
        await prompt._on_paste(events.Paste("line one\nline two\nline three"))
        await pilot.pause()
        assert prompt.text == "line one\nline two\nline three"


async def test_multiline_prompt_is_submitted_whole() -> None:
    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn)
    app = TMPromptApp(agent, FAKE_MODEL)
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.text = "first line\nsecond line"
        prompt.move_cursor(prompt.document.end)
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

    assert any(
        isinstance(message, UserMessage)
        and message.content == "first line\nsecond line"
        for message in agent.messages
    )


async def test_shift_enter_inserts_a_newline() -> None:
    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn)
    app = TMPromptApp(agent, FAKE_MODEL)
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.focus()
        await pilot.press("a")
        await pilot.press("shift+enter")
        await pilot.press("b")
        await pilot.pause()
        assert prompt.text == "a\nb"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

    assert any(
        isinstance(message, UserMessage) and message.content == "a\nb"
        for message in agent.messages
    )


async def test_escape_aborts_a_running_turn(monkeypatch) -> None:
    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn)
    app = TMPromptApp(agent, FAKE_MODEL)
    aborted: list[bool] = []
    monkeypatch.setattr(agent, "abort", lambda: aborted.append(True))

    async with app.run_test() as pilot:
        await pilot.pause()
        app._status = "working"
        app.action_dismiss_suggestions()
        await pilot.pause()

    assert aborted == [True]


async def test_escape_closes_suggestions_instead_of_aborting(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn)
    app = TMPromptApp(agent, FAKE_MODEL)
    aborted: list[bool] = []
    monkeypatch.setattr(agent, "abort", lambda: aborted.append(True))

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.text = "/mod"
        prompt.move_cursor(prompt.document.end)
        await pilot.pause()
        assert app.query_one("#suggestions").display is True

        app.action_dismiss_suggestions()
        await pilot.pause()

    assert app.suggestions_active is False
    assert aborted == []


async def test_ctrl_p_pauses_and_resumes() -> None:
    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn)
    app = TMPromptApp(agent, FAKE_MODEL)

    async with app.run_test() as pilot:
        await pilot.pause()
        app._status = "working"

        app.action_toggle_pause()
        assert agent.pause.paused is True
        assert app._status == "paused"

        app.action_toggle_pause()
        assert agent.pause.paused is False
        assert app._status == "working"


async def test_ctrl_c_stops_a_running_turn(monkeypatch) -> None:
    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn)
    app = TMPromptApp(agent, FAKE_MODEL)
    aborted: list[bool] = []
    monkeypatch.setattr(agent, "abort", lambda: aborted.append(True))

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", TextArea)
        prompt.focus()
        app._status = "working"
        await pilot.press("ctrl+c")
        await pilot.pause()

    assert aborted == [True]


async def test_aborted_end_shows_a_note() -> None:
    from tm.core.events import AgentEndEvent

    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn)
    app = TMPromptApp(agent, FAKE_MODEL)

    async with app.run_test() as pilot:
        await app._on_agent_event(AgentEndEvent(messages=[], stop_reason="aborted"))
        await pilot.pause()
        notes = app.query(".system")
        assert notes
        assert "Stopped" in str(notes.first().render())


async def test_prompt_error_resets_status(monkeypatch) -> None:
    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn)
    app = TMPromptApp(agent, FAKE_MODEL)

    async def boom(text: str) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(agent, "prompt", boom)

    async with app.run_test() as pilot:
        await app._prompt("go")
        await pilot.pause()
        assert app._status == "idle"
        notes = app.query(".system")
        assert notes
        assert "kaboom" in str(notes.first().render())
