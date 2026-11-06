from __future__ import annotations

from pydantic import BaseModel

from tm.ai.event_stream import EventStream
from tm.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from tm.core.agent import Agent
from tm.core.events import (
    AgentEndEvent,
    AgentEvent,
    AgentNoticeEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from tm.tools.base import Tool, ToolContext, ToolResult, text_result

MODEL = Model(id="fake-model", provider="fake")


class EchoParams(BaseModel):
    text: str


class EchoTool(Tool[EchoParams]):
    name = "echo"
    description = "Echo the given text back."
    parameters_model = EchoParams

    async def execute(self, call_id: str, args: EchoParams, ctx: ToolContext) -> ToolResult:
        if ctx.on_update is not None:
            await ctx.on_update("working")
        return text_result(args.text)


def make_stream(message: AssistantMessage) -> EventStream:
    stream: EventStream = EventStream()
    stream.push(StartEvent(partial=message))
    if message.text():
        stream.push(TextDeltaEvent(partial=message, delta=message.text()))
    stream.push(DoneEvent(partial=message, message=message))
    stream.end(message)
    return stream


def scripted(messages: list[AssistantMessage]):
    iterator = iter(messages)

    def stream_fn(model: Model, context: Context, options) -> EventStream:
        return make_stream(next(iterator))

    return stream_fn


async def test_agent_executes_tool_then_finishes() -> None:
    tool_call = AssistantMessage(
        tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "hi"})],
        stop_reason="tool_use",
    )
    final = AssistantMessage(content=[TextContent(text="done")], stop_reason="stop")

    agent = Agent(MODEL, stream_fn=scripted([tool_call, final]), tools=[EchoTool()])
    events: list[AgentEvent] = []
    agent.subscribe(lambda event: _record(events, event))

    await agent.prompt("please echo")

    kinds = [e.type for e in events]
    assert "agent_start" in kinds
    assert "tool_execution_start" in kinds
    assert "tool_execution_end" in kinds
    assert "agent_end" in kinds

    messages = agent.messages
    assert len(messages) == 4
    assert messages[1].role == "assistant"
    assert messages[2].role == "tool"
    assert messages[2].content[0].text == "hi"  # type: ignore[union-attr]
    assert messages[3].role == "assistant"

    end = next(e for e in events if isinstance(e, ToolExecutionEndEvent))
    assert end.is_error is False
    assert end.output == "hi"

    update = next(e for e in events if isinstance(e, ToolExecutionUpdateEvent))
    assert update.text == "working"


async def test_unknown_tool_produces_error_result() -> None:
    tool_call = AssistantMessage(
        tool_calls=[ToolCall(id="t1", name="missing", arguments={})],
        stop_reason="tool_use",
    )
    final = AssistantMessage(content=[TextContent(text="ok")], stop_reason="stop")
    agent = Agent(MODEL, stream_fn=scripted([tool_call, final]), tools=[])

    await agent.prompt("go")

    tool_message = agent.messages[2]
    assert tool_message.role == "tool"
    assert tool_message.is_error is True
    assert "Unknown tool" in tool_message.content[0].text  # type: ignore[union-attr]


async def test_invalid_arguments_produce_error_result() -> None:
    tool_call = AssistantMessage(
        tool_calls=[ToolCall(id="t1", name="echo", arguments={})],
        stop_reason="tool_use",
    )
    final = AssistantMessage(content=[TextContent(text="ok")], stop_reason="stop")
    agent = Agent(MODEL, stream_fn=scripted([tool_call, final]), tools=[EchoTool()])

    await agent.prompt("go")

    tool_message = agent.messages[2]
    assert tool_message.is_error is True  # type: ignore[union-attr]
    assert "Invalid arguments" in tool_message.content[0].text  # type: ignore[union-attr]


async def test_before_tool_call_can_block() -> None:
    tool_call = AssistantMessage(
        tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "hi"})],
        stop_reason="tool_use",
    )
    final = AssistantMessage(content=[TextContent(text="ok")], stop_reason="stop")
    agent = Agent(MODEL, stream_fn=scripted([tool_call, final]), tools=[EchoTool()])

    async def blocker(call, args):
        from tm.core.agent import BeforeToolCallResult

        return BeforeToolCallResult(block=True, reason="denied by policy")

    agent.before_tool_call = blocker
    events: list[AgentEvent] = []
    agent.subscribe(lambda event: _record(events, event))

    await agent.prompt("go")

    tool_message = agent.messages[2]
    assert tool_message.is_error is True  # type: ignore[union-attr]
    assert "denied by policy" in tool_message.content[0].text  # type: ignore[union-attr]
    assert not any(isinstance(e, ToolExecutionStartEvent) for e in events)


async def test_steering_message_is_delivered_after_turn() -> None:
    tool_call = AssistantMessage(
        tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "hi"})],
        stop_reason="tool_use",
    )
    second = AssistantMessage(content=[TextContent(text="after steering")], stop_reason="stop")
    agent = Agent(MODEL, stream_fn=scripted([tool_call, second]), tools=[EchoTool()])

    async def steer_after_start(event):
        if isinstance(event, ToolExecutionEndEvent):
            agent.steer("also do this")

    agent.subscribe(steer_after_start)
    await agent.prompt("go")

    assert any(
        m.role == "user" and getattr(m, "content", "") == "also do this"
        for m in agent.messages
    )


async def _record(events: list[AgentEvent], event: AgentEvent) -> None:
    events.append(event)


async def test_max_turns_stops_with_a_reason() -> None:
    def always_tool(model, context, options) -> EventStream:
        return make_stream(
            AssistantMessage(
                tool_calls=[ToolCall(id="t", name="echo", arguments={"text": "x"})],
                stop_reason="tool_use",
            )
        )

    agent = Agent(MODEL, stream_fn=always_tool, tools=[EchoTool()], max_turns=2)
    events: list[AgentEvent] = []
    agent.subscribe(lambda event: _record(events, event))

    await agent.prompt("go")

    assert agent.last_stop_reason == "max_turns"
    ends = [e for e in events if isinstance(e, AgentEndEvent)]
    assert ends and ends[-1].stop_reason == "max_turns"


async def test_max_turns_is_overridable_per_agent() -> None:
    def always_tool(model, context, options) -> EventStream:
        return make_stream(
            AssistantMessage(
                tool_calls=[ToolCall(id="t", name="echo", arguments={"text": "x"})],
                stop_reason="tool_use",
            )
        )

    agent = Agent(MODEL, stream_fn=always_tool, tools=[EchoTool()], max_turns=1)
    await agent.prompt("go")
    assert agent.last_stop_reason == "max_turns"
    # one turn ran: a tool-call assistant plus its result
    assert sum(1 for m in agent.messages if m.role == "assistant") == 1


async def test_transient_network_error_is_retried(monkeypatch) -> None:
    import tm.core.loop as loop

    monkeypatch.setattr(loop, "_retry_delay", lambda attempt: 0.0)
    calls = {"n": 0}

    def stream_fn(model, context, options) -> EventStream:
        calls["n"] += 1
        if calls["n"] == 1:
            message = AssistantMessage(
                model="fake",
                content=[],
                stop_reason="error",
                error_message="Connection error: timed out",
            )
        else:
            message = AssistantMessage(content=[TextContent(text="done")], stop_reason="stop")
        return make_stream(message)

    agent = Agent(MODEL, stream_fn=stream_fn, max_retries=2)
    notices: list[AgentNoticeEvent] = []

    async def listener(event: AgentEvent) -> None:
        if isinstance(event, AgentNoticeEvent):
            notices.append(event)

    agent.subscribe(listener)
    await agent.prompt("go")

    assert calls["n"] == 2
    assert agent.last_stop_reason == "stop"
    assert notices and "retrying" in notices[0].text


async def test_auth_error_is_not_retried() -> None:
    calls = {"n": 0}

    def stream_fn(model, context, options) -> EventStream:
        calls["n"] += 1
        return make_stream(
            AssistantMessage(
                model="fake",
                content=[],
                stop_reason="error",
                error_message="401 Unauthorized",
            )
        )

    agent = Agent(MODEL, stream_fn=stream_fn, max_retries=3)
    await agent.prompt("go")

    assert calls["n"] == 1
    assert agent.last_stop_reason == "error"


async def test_reasoning_only_reply_is_retried(monkeypatch) -> None:
    import tm.core.loop as loop

    monkeypatch.setattr(loop, "_retry_delay", lambda attempt: 0.0)
    calls = {"n": 0}

    def stream_fn(model, context, options) -> EventStream:
        calls["n"] += 1
        if calls["n"] == 1:
            message = AssistantMessage(
                model="fake",
                content=[ThinkingContent(thinking="still working")],
                stop_reason="stop",
                usage=Usage(output=44),
            )
        else:
            message = AssistantMessage(
                content=[TextContent(text="done")], stop_reason="stop"
            )
        return make_stream(message)

    agent = Agent(MODEL, stream_fn=stream_fn, max_retries=2)
    notices: list[AgentNoticeEvent] = []

    async def listener(event: AgentEvent) -> None:
        if isinstance(event, AgentNoticeEvent):
            notices.append(event)

    agent.subscribe(listener)
    await agent.prompt("go")

    assert calls["n"] == 2
    assert agent.last_stop_reason == "stop"
    assert notices and "retrying" in notices[0].text


async def test_empty_reply_retries_are_bounded(monkeypatch) -> None:
    import tm.core.loop as loop

    monkeypatch.setattr(loop, "_retry_delay", lambda attempt: 0.0)
    calls = {"n": 0}

    def stream_fn(model, context, options) -> EventStream:
        calls["n"] += 1
        return make_stream(
            AssistantMessage(
                model="fake",
                content=[],
                stop_reason="stop",
                usage=Usage(output=44),
            )
        )

    agent = Agent(MODEL, stream_fn=stream_fn, max_retries=2)
    events: list[AgentEvent] = []
    agent.subscribe(lambda event: _record(events, event))

    await agent.prompt("go")

    assert calls["n"] == 3
    assert agent.last_stop_reason == "empty"
    ends = [e for e in events if isinstance(e, AgentEndEvent)]
    assert ends and ends[-1].stop_reason == "empty"


async def test_output_limit_continues_instead_of_stopping() -> None:
    calls = {"n": 0}

    def stream_fn(model, context, options) -> EventStream:
        calls["n"] += 1
        if calls["n"] == 1:
            message = AssistantMessage(
                model="fake",
                content=[ThinkingContent(thinking="still working")],
                stop_reason="length",
                usage=Usage(output=8192),
            )
        else:
            message = AssistantMessage(
                content=[TextContent(text="done")], stop_reason="stop"
            )
        return make_stream(message)

    agent = Agent(MODEL, stream_fn=stream_fn, max_tokens=8192)
    notices: list[AgentNoticeEvent] = []

    async def listener(event: AgentEvent) -> None:
        if isinstance(event, AgentNoticeEvent):
            notices.append(event)

    agent.subscribe(listener)
    await agent.prompt("go")

    assert calls["n"] == 2
    assert agent.last_stop_reason == "stop"
    assert notices and "output token limit" in notices[0].text
    continuation = next(
        m
        for m in agent.messages
        if isinstance(m, UserMessage)
        and isinstance(m.content, str)
        and "cut off" in m.content
    )
    assert continuation.content
    assert isinstance(continuation.content, str)
    assert "still working" in continuation.content


async def test_continuation_request_omits_empty_assistant_message() -> None:
    calls = {"n": 0}
    requests: list[list] = []

    def stream_fn(model, context, options) -> EventStream:
        requests.append(list(context.messages))
        calls["n"] += 1
        if calls["n"] == 1:
            message = AssistantMessage(
                content=[ThinkingContent(thinking="still working")],
                stop_reason="length",
                usage=Usage(output=8192),
            )
        else:
            message = AssistantMessage(
                content=[TextContent(text="done")], stop_reason="stop"
            )
        return make_stream(message)

    agent = Agent(MODEL, stream_fn=stream_fn, max_tokens=8192)
    await agent.prompt("go")

    assert len(requests) == 2
    assert not any(
        isinstance(message, AssistantMessage)
        and not message.tool_calls
        and not message.text()
        for message in requests[1]
    )
    assert any(
        isinstance(message, UserMessage)
        and isinstance(message.content, str)
        and "cut off" in message.content
        for message in requests[1]
    )
    # The session still keeps the truncated turn for display and recovery.
    assert any(
        isinstance(message, AssistantMessage) and message.stop_reason == "length"
        for message in agent.messages
    )


async def test_output_limit_continuation_is_bounded() -> None:
    calls = {"n": 0}

    def stream_fn(model, context, options) -> EventStream:
        calls["n"] += 1
        return make_stream(
            AssistantMessage(
                content=[ThinkingContent(thinking="still working")],
                stop_reason="length",
                usage=Usage(output=8192),
            )
        )

    agent = Agent(
        MODEL,
        stream_fn=stream_fn,
        max_tokens=8192,
        max_length_continues=2,
        max_length_stalls=0,
    )
    await agent.prompt("go")

    assert calls["n"] == 3
    assert agent.last_stop_reason == "length"


async def test_stalled_continuations_stop_early() -> None:
    calls = {"n": 0}

    def stream_fn(model, context, options) -> EventStream:
        calls["n"] += 1
        return make_stream(
            AssistantMessage(
                content=[ThinkingContent(thinking="still working")],
                stop_reason="length",
                usage=Usage(output=8192),
            )
        )

    agent = Agent(
        MODEL,
        stream_fn=stream_fn,
        max_tokens=8192,
        max_length_continues=5,
        max_length_stalls=2,
    )
    await agent.prompt("go")

    # The first truncated turn is followed by one continuation. With no
    # tool call or text after that, the second stalled turn stops the run.
    assert calls["n"] == 2
    assert agent.last_stop_reason == "length"


async def test_first_continuation_forces_a_tool_call() -> None:
    calls = {"n": 0}
    choices: list[str | None] = []

    def stream_fn(model, context, options) -> EventStream:
        calls["n"] += 1
        choices.append(options.tool_choice)
        if calls["n"] == 1:
            message = AssistantMessage(
                content=[ThinkingContent(thinking="still working")],
                stop_reason="length",
                usage=Usage(output=8192),
            )
        else:
            message = AssistantMessage(
                content=[TextContent(text="done")], stop_reason="stop"
            )
        return make_stream(message)

    agent = Agent(MODEL, stream_fn=stream_fn, tools=[EchoTool()], max_tokens=8192)
    await agent.prompt("go")

    assert choices == [None, "required"]
    assert agent.last_stop_reason == "stop"


async def test_unsupported_tool_choice_falls_back() -> None:
    calls = {"n": 0}
    choices: list[str | None] = []

    def stream_fn(model, context, options) -> EventStream:
        calls["n"] += 1
        choices.append(options.tool_choice)
        if calls["n"] == 1:
            message = AssistantMessage(
                content=[ThinkingContent(thinking="still working")],
                stop_reason="length",
                usage=Usage(output=8192),
            )
        elif calls["n"] == 2:
            message = AssistantMessage(
                stop_reason="error",
                error_message="Invalid tool_choice: required",
            )
        else:
            message = AssistantMessage(
                content=[TextContent(text="done")], stop_reason="stop"
            )
        return make_stream(message)

    agent = Agent(MODEL, stream_fn=stream_fn, tools=[EchoTool()], max_tokens=8192)
    await agent.prompt("go")

    assert calls["n"] == 3
    assert choices == [None, "required", None]
    assert agent.last_stop_reason == "stop"


async def test_streaming_repetition_stops_the_run() -> None:
    text = "Running.\n" * 300

    def stream_fn(model, context, options) -> EventStream:
        stream: EventStream = EventStream()
        first = AssistantMessage(content=[TextContent(text=text[:200])])
        stream.push(StartEvent(partial=first))
        for end in range(400, len(text) + 1, 400):
            partial = AssistantMessage(content=[TextContent(text=text[:end])])
            stream.push(TextDeltaEvent(partial=partial, delta=text[end - 400 : end]))
        final = AssistantMessage(content=[TextContent(text=text)], stop_reason="stop")
        stream.push(DoneEvent(partial=final, message=final))
        stream.end(final)
        return stream

    agent = Agent(MODEL, stream_fn=stream_fn)
    events: list[AgentEvent] = []
    agent.subscribe(lambda event: _record(events, event))

    await agent.prompt("go")

    assert agent.last_stop_reason == "loop"
    ends = [e for e in events if isinstance(e, AgentEndEvent)]
    assert ends and ends[-1].stop_reason == "loop"


async def test_raw_tool_markup_stops_the_run() -> None:
    leaked = AssistantMessage(
        content=[
            TextContent(
                text=(
                    "Let me call the tool now.\n\n<to\n</parameter>\nread."
                )
            )
        ],
        stop_reason="stop",
    )
    agent = Agent(MODEL, stream_fn=scripted([leaked]))
    events: list[AgentEvent] = []
    agent.subscribe(lambda event: _record(events, event))

    await agent.prompt("go")

    assert agent.last_stop_reason == "tool_parser"
    ends = [e for e in events if isinstance(e, AgentEndEvent)]
    assert ends and ends[-1].stop_reason == "tool_parser"


async def test_repeated_identical_tool_calls_stop_the_run() -> None:
    calls = {"n": 0}

    def stream_fn(model, context, options) -> EventStream:
        calls["n"] += 1
        return make_stream(
            AssistantMessage(
                tool_calls=[ToolCall(id=f"t{calls['n']}", name="echo", arguments={"text": "same"})],
                stop_reason="tool_use",
            )
        )

    agent = Agent(
        MODEL,
        stream_fn=stream_fn,
        tools=[EchoTool()],
        max_turns=20,
        max_repeated_tool_calls=3,
    )
    events: list[AgentEvent] = []
    agent.subscribe(lambda event: _record(events, event))

    await agent.prompt("go")

    assert calls["n"] == 3
    assert agent.last_stop_reason == "loop"
    ends = [e for e in events if isinstance(e, AgentEndEvent)]
    assert ends and ends[-1].stop_reason == "loop"


async def test_truncated_tool_call_is_not_executed() -> None:
    calls = {"n": 0}

    def stream_fn(model, context, options) -> EventStream:
        calls["n"] += 1
        if calls["n"] == 1:
            message = AssistantMessage(
                tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "hi"})],
                stop_reason="length",
                usage=Usage(output=8192),
            )
        else:
            message = AssistantMessage(
                content=[TextContent(text="done")], stop_reason="stop"
            )
        return make_stream(message)

    agent = Agent(MODEL, stream_fn=stream_fn, tools=[EchoTool()], max_tokens=8192)
    events: list[AgentEvent] = []
    agent.subscribe(lambda event: _record(events, event))

    await agent.prompt("go")

    assert calls["n"] == 2
    assert not any(isinstance(e, ToolExecutionStartEvent) for e in events)
    results = [m for m in agent.messages if isinstance(m, ToolResultMessage)]
    assert len(results) == 1
    assert results[0].is_error is True
    assert "cut off" in results[0].text()
    assert isinstance(agent.messages[-1], AssistantMessage)
    assert agent.messages[-1].text() == "done"


async def test_a_broken_listener_does_not_abort_the_run() -> None:
    final = AssistantMessage(content=[TextContent(text="done")], stop_reason="stop")
    agent = Agent(MODEL, stream_fn=scripted([final]))

    async def boom(event: AgentEvent) -> None:
        raise RuntimeError("listener boom")

    collected: list[AgentEvent] = []

    async def collect(event: AgentEvent) -> None:
        collected.append(event)

    agent.subscribe(boom)
    agent.subscribe(collect)

    await agent.prompt("go")

    assert agent.last_stop_reason == "stop"
    assert any(isinstance(event, AgentEndEvent) for event in collected)


async def test_a_broken_permission_hook_does_not_hang() -> None:
    tool_call = AssistantMessage(
        tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "hi"})],
        stop_reason="tool_use",
    )
    final = AssistantMessage(content=[TextContent(text="done")], stop_reason="stop")
    agent = Agent(MODEL, stream_fn=scripted([tool_call, final]), tools=[EchoTool()])

    async def boom(call, args) -> None:
        raise RuntimeError("gate down")

    agent.before_tool_call = boom

    await agent.prompt("go")

    assert agent.last_stop_reason == "stop"
    assert any(
        isinstance(message, ToolResultMessage) and message.is_error
        for message in agent.messages
    )


async def test_loop_stop_drops_the_poisoned_turn_from_history() -> None:
    text = "Let me run.\n" * 200

    def stream_fn(model, context, options) -> EventStream:
        stream: EventStream = EventStream()
        message = AssistantMessage(content=[TextContent(text=text)], stop_reason="stop")
        stream.push(StartEvent(partial=message))
        stream.push(TextDeltaEvent(partial=message, delta=text))
        stream.push(DoneEvent(partial=message, message=message))
        stream.end(message)
        return stream

    agent = Agent(MODEL, stream_fn=stream_fn, max_turns=5)
    await agent.prompt("go")

    assert agent.last_stop_reason == "loop"
    assert [message.role for message in agent.messages] == ["user"]


async def test_loop_stop_does_not_persist_the_poisoned_turn(tmp_path) -> None:
    from pathlib import Path

    from tm.core.session import SessionManager

    session = SessionManager(tmp_path).create(cwd=Path(tmp_path))
    text = "Let me run.\n" * 200

    def stream_fn(model, context, options) -> EventStream:
        stream: EventStream = EventStream()
        message = AssistantMessage(content=[TextContent(text=text)], stop_reason="stop")
        stream.push(StartEvent(partial=message))
        stream.push(TextDeltaEvent(partial=message, delta=text))
        stream.push(DoneEvent(partial=message, message=message))
        stream.end(message)
        return stream

    agent = Agent(MODEL, stream_fn=stream_fn, session=session, max_turns=5)
    await agent.prompt("go")

    stored = session.messages()
    assert agent.last_stop_reason == "loop"
    assert [message.role for message in stored] == ["user"]
    assert not any(
        isinstance(message, AssistantMessage) and "Let me run" in message.text()
        for message in stored
    )


async def test_tool_loop_stop_keeps_one_call_group_and_continues_clean() -> None:
    calls = {"n": 0}
    requests: list[list] = []

    def stream_fn(model, context, options) -> EventStream:
        requests.append(list(context.messages))
        calls["n"] += 1
        return make_stream(
            AssistantMessage(
                tool_calls=[
                    ToolCall(
                        id=f"t{calls['n']}", name="echo", arguments={"text": "same"}
                    )
                ],
                stop_reason="tool_use",
            )
        )

    agent = Agent(
        MODEL,
        stream_fn=stream_fn,
        tools=[EchoTool()],
        max_turns=20,
        max_repeated_tool_calls=3,
    )
    await agent.prompt("go")

    assert agent.last_stop_reason == "loop"
    kept_calls = [
        call
        for message in agent.messages
        if isinstance(message, AssistantMessage)
        for call in message.tool_calls
    ]
    assert [call.arguments for call in kept_calls] == [{"text": "same"}]

    await agent.prompt("继续")
    assert agent.last_stop_reason == "loop"
    sent_calls = [
        call
        for message in requests[-1]
        if isinstance(message, AssistantMessage)
        for call in message.tool_calls
    ]
    assert len(sent_calls) == 1


async def test_rephrased_planning_loop_stops_and_is_cleaned() -> None:
    text = "\n".join(
        f"Let me run step {index} of the plan carefully." for index in range(12)
    )

    def stream_fn(model, context, options) -> EventStream:
        stream: EventStream = EventStream()
        message = AssistantMessage(content=[TextContent(text=text)], stop_reason="stop")
        stream.push(StartEvent(partial=message))
        stream.push(TextDeltaEvent(partial=message, delta=text))
        stream.push(DoneEvent(partial=message, message=message))
        stream.end(message)
        return stream

    agent = Agent(MODEL, stream_fn=stream_fn, max_turns=5)
    await agent.prompt("go")

    assert agent.last_stop_reason == "loop"
    assert [message.role for message in agent.messages] == ["user"]
