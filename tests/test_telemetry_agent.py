"""Integration test: the agent emits redacted telemetry spans."""

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
    ToolCall,
)
from tm.core.agent import Agent
from tm.telemetry import SPAN_OPERATION, SPAN_TOOL, SPAN_TURN, MemoryTelemetry
from tm.tools.base import Tool, ToolContext, ToolResult, text_result

MODEL = Model(id="fake-model", provider="fake")


class EchoParams(BaseModel):
    text: str


class EchoTool(Tool[EchoParams]):
    name = "echo"
    description = "Echo the given text back."
    parameters_model = EchoParams

    async def execute(self, call_id: str, args: EchoParams, ctx: ToolContext) -> ToolResult:
        return text_result(args.text)


def _make_stream(message: AssistantMessage) -> EventStream:
    stream: EventStream = EventStream()
    stream.push(StartEvent(partial=message))
    stream.push(DoneEvent(partial=message, message=message))
    stream.end(message)
    return stream


def _scripted(messages: list[AssistantMessage]):
    iterator = iter(messages)

    def stream_fn(model: Model, context: Context, options) -> EventStream:
        return _make_stream(next(iterator))

    return stream_fn


async def test_agent_emits_operation_turn_and_tool_spans() -> None:
    tool_call = AssistantMessage(
        tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "hi"})],
        stop_reason="tool_use",
    )
    final = AssistantMessage(content=[TextContent(text="done")], stop_reason="stop")

    telemetry = MemoryTelemetry()
    agent = Agent(
        MODEL,
        stream_fn=_scripted([tool_call, final]),
        tools=[EchoTool()],
        telemetry=telemetry,
    )
    await agent.prompt("please echo")

    names = [span.name for span in telemetry.spans]
    assert names.count(SPAN_OPERATION) == 1
    assert names.count(SPAN_TOOL) == 1
    assert names.count(SPAN_TURN) == 2
    assert all(span.status == "ok" for span in telemetry.spans)


async def test_tool_span_never_carries_arguments() -> None:
    tool_call = AssistantMessage(
        tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "top secret"})],
        stop_reason="tool_use",
    )
    final = AssistantMessage(content=[TextContent(text="done")], stop_reason="stop")

    telemetry = MemoryTelemetry()
    agent = Agent(
        MODEL,
        stream_fn=_scripted([tool_call, final]),
        tools=[EchoTool()],
        telemetry=telemetry,
    )
    await agent.prompt("go")

    tool_span = next(s for s in telemetry.spans if s.name == SPAN_TOOL)
    assert tool_span.attributes == {"tool": "echo"}
    assert "top secret" not in str(tool_span.attributes)
