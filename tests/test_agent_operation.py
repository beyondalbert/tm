"""Integration tests: the agent maintains a durable operation restart point."""

from __future__ import annotations

from pydantic import BaseModel

from tm.ai.event_stream import EventStream
from tm.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    ErrorEvent,
    Model,
    StartEvent,
    TextContent,
    ToolCall,
)
from tm.core.agent import Agent
from tm.core.operation import Operation, OperationStatus, PendingEffect
from tm.core.store import Store, operation_result
from tm.tools.base import Tool, ToolContext, ToolResult, text_result
from tm.tools.registry import build_default_tools

MODEL = Model(id="fake-model", provider="fake")


def _stream(message: AssistantMessage) -> EventStream:
    stream: EventStream = EventStream()
    stream.push(StartEvent(partial=message))
    if message.text():
        from tm.ai.types import TextDeltaEvent

        stream.push(TextDeltaEvent(partial=message, delta=message.text()))
    stream.push(DoneEvent(partial=message, message=message))
    stream.end(message)
    return stream


def _scripted(messages: list[AssistantMessage]):
    iterator = iter(messages)

    def stream_fn(model: Model, context: Context, options) -> EventStream:
        return _stream(next(iterator))

    return stream_fn


def _failing_stream(model: Model, context: Context, options) -> EventStream:
    message = AssistantMessage(model="fake", stop_reason="error", error_message="boom")
    stream: EventStream = EventStream()
    stream.push(ErrorEvent(error="boom", message=message, partial=message))
    stream.end(message)
    return stream


class SpyParams(BaseModel):
    pass


class SpyTool(Tool[SpyParams]):
    name = "spy"
    description = "Record the durable pending effect observed while running."
    parameters_model = SpyParams

    def __init__(self, store: Store, observed: dict) -> None:
        self._store = store
        self._observed = observed

    async def execute(self, call_id: str, args: SpyParams, ctx: ToolContext) -> ToolResult:
        pending = Operation.pending(self._store)
        self._observed["pending"] = pending[0].pending if pending else None
        return text_result("ok")


async def test_tool_runs_between_intent_and_settlement() -> None:
    store = Store()
    observed: dict = {}
    tool_call = AssistantMessage(
        tool_calls=[ToolCall(id="t1", name="spy", arguments={})], stop_reason="tool_use"
    )
    final = AssistantMessage(content=[TextContent(text="done")], stop_reason="stop")
    agent = Agent(
        MODEL,
        stream_fn=_scripted([tool_call, final]),
        tools=[SpyTool(store, observed)],
        store=store,
    )

    await agent.prompt("go")

    pending = observed["pending"]
    assert isinstance(pending, PendingEffect)
    assert pending.tool_name == "spy"
    assert pending.call_id == "t1"
    assert pending.replay_safe is False
    # settled once the effect finished
    assert Operation.pending(store) == []


async def test_completed_run_writes_result() -> None:
    store = Store()
    final = AssistantMessage(content=[TextContent(text="hi")], stop_reason="stop")
    agent = Agent(MODEL, stream_fn=_scripted([final]), store=store)

    await agent.prompt("hello")

    assert agent.operation is not None
    result = store.get_value(operation_result(agent.operation.operation_id))
    assert result is not None
    assert result["status"] == OperationStatus.COMPLETED.value
    assert result["kind"] == "run"


async def test_failed_run_settles_failed() -> None:
    store = Store()
    agent = Agent(MODEL, stream_fn=_failing_stream, store=store)

    await agent.prompt("hello")

    assert agent.operation is not None
    result = store.get_value(operation_result(agent.operation.operation_id))
    assert result is not None
    assert result["status"] == OperationStatus.FAILED.value
    assert Operation.pending(store) == []


async def test_no_store_means_no_operation() -> None:
    final = AssistantMessage(content=[TextContent(text="hi")], stop_reason="stop")
    agent = Agent(MODEL, stream_fn=_scripted([final]))
    await agent.prompt("hello")
    assert agent.operation is None


def test_read_only_tools_are_replay_safe() -> None:
    tools = {tool.name: tool for tool in build_default_tools()}
    for name in ("read", "grep", "find", "ls"):
        assert tools[name].replay_safe is True
    for name in ("write", "edit", "shell"):
        assert tools[name].replay_safe is False
