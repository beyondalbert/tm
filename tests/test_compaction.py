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
