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
    Usage,
    UserMessage,
)
from tm.core.agent import Agent
from tm.core.compaction import (
    DEFAULT_INSTRUCTIONS,
    SUMMARY_PREFIX,
    estimate_context_tokens,
    extract_file_operations,
    format_file_operations,
    format_transcript,
)
from tm.core.events import AgentNoticeEvent

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


async def test_compact_keeps_tool_call_with_its_result() -> None:
    agent = Agent(MODEL, stream_fn=summary_stream("summary"))
    agent.set_messages(
        [
            UserMessage(content="q"),
            AssistantMessage(
                content=[TextContent(text="call")],
                tool_calls=[ToolCall(id="t1", name="read", arguments={})],
            ),
            ToolResultMessage(
                tool_call_id="t1", tool_name="read", content=[TextContent(text="data")]
            ),
            UserMessage(content="next"),
            AssistantMessage(content=[TextContent(text="done")]),
        ]
    )

    assert await agent.compact(keep_recent=3) is True
    messages = agent.messages
    # summary first; the retained tail must not start with an orphan tool result
    assert isinstance(messages[0], UserMessage)
    assert not isinstance(messages[1], ToolResultMessage)
    # the tool call and its result are both retained
    assert any(isinstance(m, AssistantMessage) and m.tool_calls for m in messages)
    assert any(isinstance(m, ToolResultMessage) for m in messages)


async def test_orphan_tool_result_is_not_sent_to_the_provider() -> None:
    seen: dict[str, list[Message]] = {}

    def stream_fn(model, context, options) -> EventStream:
        seen["messages"] = list(context.messages)
        message = AssistantMessage(content=[TextContent(text="ok")], stop_reason="stop")
        stream: EventStream = EventStream()
        stream.push(StartEvent(partial=message))
        stream.push(DoneEvent(partial=message, message=message))
        stream.end(message)
        return stream

    agent = Agent(MODEL, stream_fn=stream_fn)
    agent.set_messages(
        [
            ToolResultMessage(
                tool_call_id="ghost", tool_name="read", content=[TextContent(text="stale")]
            ),
            UserMessage(content="hi"),
        ]
    )

    await agent.prompt("go")

    assert not any(isinstance(m, ToolResultMessage) for m in seen["messages"])


async def test_overflow_recovers_more_than_once() -> None:
    calls = {"n": 0}

    def stream_fn(model, context, options) -> EventStream:
        calls["n"] += 1
        index = calls["n"]
        if index in (1, 3):
            message = AssistantMessage(content=[TextContent(text="partial")], stop_reason="length")
        elif index in (2, 4):
            message = AssistantMessage(
                content=[TextContent(text="summary")], stop_reason="stop"
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

    # length -> compact -> length -> compact -> done
    assert calls["n"] == 5
    last = agent.messages[-1]
    assert isinstance(last, AssistantMessage)
    assert last.text() == "done"


async def test_auto_compact_emits_a_notice() -> None:
    small = Model(id="fake", provider="fake", context_window=100)
    agent = Agent(
        small,
        stream_fn=summary_stream("summary"),
        auto_compact=True,
        compact_threshold=0.5,
        compact_keep_recent=4,
    )
    notices: list[str] = []

    async def listener(event) -> None:
        if isinstance(event, AgentNoticeEvent):
            notices.append(event.text)

    agent.subscribe(listener)
    agent.set_messages([UserMessage(content="word " * 20) for _ in range(20)])

    await agent.prompt("q")

    assert any("compacting" in text for text in notices)


async def test_overflow_compaction_shrinks_the_retained_tail(monkeypatch) -> None:
    def always_length(model, context, options) -> EventStream:
        message = AssistantMessage(content=[TextContent(text="partial")], stop_reason="length")
        stream: EventStream = EventStream()
        stream.push(StartEvent(partial=message))
        stream.push(DoneEvent(partial=message, message=message))
        stream.end(message)
        return stream

    agent = Agent(MODEL, stream_fn=always_length, compact_keep_recent=3)
    keeps: list[int] = []

    async def spy(*, keep_recent: int = 6, signal=None) -> bool:
        keeps.append(keep_recent)
        return True

    monkeypatch.setattr(agent, "compact", spy)
    agent.set_messages([UserMessage(content=f"m{i}") for i in range(10)])

    await agent.prompt("go")

    assert keeps[0] == 2
    assert keeps[-1] == 0


def test_format_transcript_truncates_to_budget() -> None:
    messages: list[Message] = [UserMessage(content="x" * 100) for _ in range(10)]
    text = format_transcript(messages, max_chars=100)
    assert text.startswith("... (earlier part truncated)")
    assert len(text) <= len("... (earlier part truncated)\n") + 100


def test_estimate_context_tokens_prefers_provider_usage() -> None:
    messages: list[Message] = [
        UserMessage(content="x" * 400),
        AssistantMessage(
            content=[TextContent(text="hi")],
            stop_reason="stop",
            usage=Usage(input=1000, output=50, total=1050),
        ),
        UserMessage(content="y" * 400),
    ]
    total = estimate_context_tokens(messages)
    assert total >= 1050
    assert total <= 1050 + 200


def test_estimate_context_tokens_ignores_failed_usage() -> None:
    messages: list[Message] = [
        AssistantMessage(
            content=[TextContent(text="boom")],
            stop_reason="error",
            usage=Usage(input=9999, output=1, total=10000),
        ),
    ]
    # No successful usage: falls back to the char heuristic (far below 10000).
    assert estimate_context_tokens(messages) < 10


def test_structured_summary_prompt_has_the_expected_sections() -> None:
    for heading in ("## Goal", "## Progress", "## Next Steps", "## Critical Context"):
        assert heading in DEFAULT_INSTRUCTIONS


def test_extract_and_format_file_operations() -> None:
    messages: list[Message] = [
        AssistantMessage(
            tool_calls=[ToolCall(id="1", name="read", arguments={"path": "a.py"})],
            stop_reason="tool_use",
        ),
        AssistantMessage(
            tool_calls=[ToolCall(id="2", name="edit", arguments={"path": "b.py"})],
            stop_reason="tool_use",
        ),
        AssistantMessage(
            tool_calls=[ToolCall(id="3", name="read", arguments={"path": "b.py"})],
            stop_reason="tool_use",
        ),
    ]
    read_files, modified_files = extract_file_operations(messages)
    assert read_files == ["a.py"]
    assert modified_files == ["b.py"]

    text = format_file_operations(read_files, modified_files)
    assert "<read-files>" in text and "a.py" in text
    assert "<modified-files>" in text and "b.py" in text


async def test_compact_reuses_the_previous_summary(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_summarize(
        stream_fn, model, messages, instructions=None, signal=None, previous_summary=None
    ) -> str:
        captured["previous"] = previous_summary
        captured["messages"] = list(messages)
        return "updated summary"

    monkeypatch.setattr("tm.core.compaction.summarize_messages", fake_summarize)

    agent = Agent(MODEL, stream_fn=summary_stream("x"))
    agent.set_messages(
        [
            UserMessage(content=f"{SUMMARY_PREFIX}old summary"),
            UserMessage(content="m1"),
            UserMessage(content="m2"),
            UserMessage(content="m3"),
        ]
    )

    assert await agent.compact(keep_recent=1) is True
    assert captured["previous"] == "old summary"
    summarized = captured["messages"]
    assert isinstance(summarized, list)
    assert all(
        not (isinstance(m.content, str) and "old summary" in m.content) for m in summarized
    )


def test_trim_oldest_halves_the_history() -> None:
    agent = Agent(MODEL, stream_fn=summary_stream("s"))
    agent.set_messages([UserMessage(content=str(i)) for i in range(10)])

    assert agent._trim_oldest() is True
    assert len(agent.messages) <= 6


async def test_overflow_falls_back_to_trimming_when_compaction_fails(monkeypatch) -> None:
    calls = {"n": 0}

    def stream_fn(model, context, options) -> EventStream:
        calls["n"] += 1
        if calls["n"] <= 2:
            message = AssistantMessage(
                content=[TextContent(text="partial")], stop_reason="length"
            )
        else:
            message = AssistantMessage(
                content=[TextContent(text="done")], stop_reason="stop"
            )
        stream: EventStream = EventStream()
        stream.push(StartEvent(partial=message))
        stream.push(DoneEvent(partial=message, message=message))
        stream.end(message)
        return stream

    agent = Agent(MODEL, stream_fn=stream_fn)

    async def fail_compact(*, keep_recent: int = 6, signal=None) -> bool:
        return False

    monkeypatch.setattr(agent, "compact", fail_compact)
    agent.set_messages([UserMessage(content=f"m{i}") for i in range(10)])

    await agent.prompt("go")

    last = agent.messages[-1]
    assert isinstance(last, AssistantMessage)
    assert last.text() == "done"
    assert len(agent.messages) < 12

