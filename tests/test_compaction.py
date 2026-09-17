from __future__ import annotations

from tm.ai.event_stream import EventStream
from tm.ai.types import (
    AssistantMessage,
    DoneEvent,
    Message,
    Model,
    StartEvent,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from tm.core.agent import Agent
from tm.core.compaction import format_transcript

MODEL = Model(id="fake", provider="fake")


def summary_stream(text: str):
    def stream_fn(model, context, options) -> EventStream:
        message = AssistantMessage(content=[TextContent(text=text)], stop_reason="stop")
        stream: EventStream = EventStream()
        stream.push(StartEvent(partial=message))
        stream.push(DoneEvent(partial=message, message=message))
        stream.end(message)
        return stream

    return stream_fn


def test_format_transcript_renders_roles() -> None:
    messages: list[Message] = [
        UserMessage(content="hello"),
        AssistantMessage(
            content=[TextContent(text="reading")],
            tool_calls=[ToolCall(id="t1", name="read", arguments={"path": "a.txt"})],
        ),
        ToolResultMessage(tool_call_id="t1", tool_name="read", content=[TextContent(text="data")]),
    ]
    transcript = format_transcript(messages)
    assert "USER: hello" in transcript
    assert "ASSISTANT: reading" in transcript
    assert "calls read" in transcript
    assert "TOOL read: data" in transcript


async def test_compact_replaces_older_messages() -> None:
    agent = Agent(MODEL, stream_fn=summary_stream("earlier summary"))
    agent.set_messages([UserMessage(content=f"m{i}") for i in range(10)])

    changed = await agent.compact(keep_recent=4)

    assert changed is True
    messages = agent.messages
    assert len(messages) == 5
    assert isinstance(messages[0], UserMessage)
    assert "earlier summary" in messages[0].content
    assert [m.content for m in messages[1:]] == ["m6", "m7", "m8", "m9"]


async def test_compact_noop_when_short() -> None:
    agent = Agent(MODEL, stream_fn=summary_stream("x"))
    agent.set_messages([UserMessage(content="only one")])
    assert await agent.compact(keep_recent=6) is False


async def test_reset_clears_messages() -> None:
    agent = Agent(MODEL, stream_fn=summary_stream("x"))
    agent.set_messages([UserMessage(content="a")])
    agent.reset()
    assert agent.messages == []


async def test_auto_compact_triggers_over_threshold() -> None:
    small = Model(id="fake", provider="fake", context_window=100)
    agent = Agent(
        small,
        stream_fn=summary_stream("summary"),
        auto_compact=True,
        compact_threshold=0.5,
        compact_keep_recent=4,
    )
    agent.set_messages([UserMessage(content="word " * 20) for _ in range(20)])

    await agent.prompt("new question")

    messages = agent.messages
    assert len(messages) == 7
    assert isinstance(messages[0], UserMessage)
    assert "summary" in messages[0].content


async def test_auto_compact_disabled_keeps_history() -> None:
    small = Model(id="fake", provider="fake", context_window=100)
    agent = Agent(
        small,
        stream_fn=summary_stream("summary"),
        auto_compact=False,
        compact_threshold=0.5,
        compact_keep_recent=4,
    )
    agent.set_messages([UserMessage(content="word " * 20) for _ in range(20)])

    await agent.prompt("new question")

    assert len(agent.messages) == 22


async def test_auto_compact_skips_when_short() -> None:
    small = Model(id="fake", provider="fake", context_window=100)
    agent = Agent(
        small,
        stream_fn=summary_stream("summary"),
        auto_compact=True,
        compact_threshold=0.5,
        compact_keep_recent=4,
    )
    agent.set_messages([UserMessage(content="short")])
    await agent.prompt("q")
    assert len(agent.messages) == 3


async def test_compaction_is_persisted_and_restored(tmp_path) -> None:
    from tm.core.session import SessionManager

    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path)
    for index in range(10):
        session.append(UserMessage(content=f"m{index}"))

    agent = Agent(MODEL, stream_fn=summary_stream("earlier summary"), session=session)
    assert await agent.compact(keep_recent=3) is True

    messages = agent.messages
    assert messages[0].content.startswith("Summary of earlier conversation:")  # type: ignore[union-attr]
    assert [m.content for m in messages[1:]] == ["m7", "m8", "m9"]

    reopened = manager.open(session.path)
    restored = reopened.messages()
    assert restored[0].content.startswith("Summary of earlier conversation:")  # type: ignore[union-attr]
    assert [m.content for m in restored[1:]] == ["m7", "m8", "m9"]


async def test_compact_hooks_are_called() -> None:
    seen: dict[str, object] = {}
    agent = Agent(MODEL, stream_fn=summary_stream("s"))
    agent.set_messages([UserMessage(content=f"m{i}") for i in range(10)])

    async def before(older):
        seen["before"] = len(older)

    def after(summary):
        seen["after"] = summary

    agent.before_compact = before
    agent.after_compact = after

    assert await agent.compact(keep_recent=4) is True
    assert seen["before"] == 6
    assert seen["after"] == "s"


async def test_overflow_recovery_compacts_and_retries() -> None:
    calls = {"n": 0}

    def stream_fn(model, context, options) -> EventStream:
        calls["n"] += 1
        index = calls["n"]
        if index == 1:
            message = AssistantMessage(content=[TextContent(text="partial")], stop_reason="length")
        elif index == 2:
            message = AssistantMessage(
                content=[TextContent(text="earlier summary")], stop_reason="stop"
            )
        else:
            message = AssistantMessage(content=[TextContent(text="done")], stop_reason="stop")
        stream: EventStream = EventStream()
        stream.push(StartEvent(partial=message))
        stream.push(DoneEvent(partial=message, message=message))
        stream.end(message)
        return stream

    agent = Agent(MODEL, stream_fn=stream_fn)
    agent.set_messages([UserMessage(content=f"m{i}") for i in range(10)])

    await agent.prompt("go")

    assert calls["n"] == 3  # length, summary, retried turn
    last = agent.messages[-1]
    assert isinstance(last, AssistantMessage)
    assert last.text() == "done"

