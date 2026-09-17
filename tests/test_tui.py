from __future__ import annotations

import pytest

pytest.importorskip("textual")

from pydantic import BaseModel  # noqa: E402
from textual.app import App  # noqa: E402
from textual.widgets import Input  # noqa: E402

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
        prompt = app.query_one("#prompt", Input)
        prompt.value = "do something"
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
        prompt = app.query_one("#prompt", Input)
        prompt.value = "/greet"
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
        prompt = app.query_one("#prompt", Input)
        prompt.value = "go"
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
        prompt = app.query_one("#prompt", Input)
        prompt.value = "go"
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


async def test_footer_refreshes_after_model_command() -> None:
    agent = Agent(FAKE_MODEL, stream_fn=fake_stream_fn)
    app = TMPromptApp(agent, FAKE_MODEL)

    async def handler(text: str) -> str | None:
        if text.startswith("model "):
            agent.model = Model(id="glm-4-plus", provider="zhipu")
        return None

    app.command_handler = handler
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        prompt.value = "/model glm-4-plus"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert "glm-4-plus" in str(app.query_one("#footer").render())


async def test_tool_output_truncates_and_ctrl_o_expands() -> None:
    tool_turn = AssistantMessage(
        tool_calls=[ToolCall(id="t1", name="long", arguments={})],
        stop_reason="tool_use",
    )
    final = AssistantMessage(content=[TextContent(text="ok")], stop_reason="stop")
    agent = Agent(FAKE_MODEL, stream_fn=scripted([tool_turn, final]), tools=[LongTool()])
    app = TMPromptApp(agent, FAKE_MODEL)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        prompt.value = "go"
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        body = app.query_one(".tool .tool-body")
        assert "ctrl+o to expand" in str(body.render())
        assert "line 19" in str(body.render())
        assert "line 20" not in str(body.render())

        app.action_toggle_tools()
        await pilot.pause()
        body = app.query_one(".tool .tool-body")
        assert "ctrl+o to expand" not in str(body.render())
        assert "line 39" in str(body.render())


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

        prompt = app.query_one("#prompt", Input)
        prompt.value = "/new"
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert len(app.query(".user")) == 0
