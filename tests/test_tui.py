from __future__ import annotations

import pytest

pytest.importorskip("textual")

from textual.app import App  # noqa: E402
from textual.widgets import Input  # noqa: E402

from tm.ai.event_stream import EventStream  # noqa: E402
from tm.ai.types import (  # noqa: E402
    AssistantMessage,
    DoneEvent,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    UserMessage,
)
from tm.core.agent import Agent  # noqa: E402
from tm.core.session import SessionInfo  # noqa: E402
from tm.permissions import ApprovalOutcome  # noqa: E402
from tm.tui.app import PermissionScreen, SessionScreen, TMPromptApp  # noqa: E402

FAKE_MODEL = Model(id="fake", provider="fake")


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
