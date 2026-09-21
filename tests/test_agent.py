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
    ToolCall,
)
from tm.core.agent import Agent
from tm.core.events import (
    AgentEndEvent,
    AgentEvent,
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
