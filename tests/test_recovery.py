"""Tests for automatic recovery of interrupted durable operations."""

from __future__ import annotations

from pathlib import Path

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
    ToolResultMessage,
    UserMessage,
)
from tm.core.agent import Agent
from tm.core.operation import Operation, OperationStatus, PendingEffect
from tm.core.session import Session, SessionManager
from tm.core.store import Store, operation_result
from tm.tools.base import Tool, ToolContext, ToolResult, text_result

MODEL = Model(id="fake", provider="fake")


class NoParams(BaseModel):
    pass


class ReplayTool(Tool[NoParams]):
    name = "rt"
    description = "Replay-safe effect."
    parameters_model = NoParams
    replay_safe = True

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, call_id: str, args: NoParams, ctx: ToolContext) -> ToolResult:
        self.calls += 1
        return text_result("replayed")


class OpaqueTool(Tool[NoParams]):
    name = "ot"
    description = "Not replay-safe."
    parameters_model = NoParams
    replay_safe = False

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, call_id: str, args: NoParams, ctx: ToolContext) -> ToolResult:
        self.calls += 1
        return text_result("ran")


def _final(text: str):
    def stream_fn(model: Model, context: Context, options) -> EventStream:
        message = AssistantMessage(content=[TextContent(text=text)], stop_reason="stop")
        stream: EventStream = EventStream()
        stream.push(StartEvent(partial=message))
        stream.push(TextDeltaEvent(partial=message, delta=text))
        stream.push(DoneEvent(partial=message, message=message))
        stream.end(message)
        return stream

    return stream_fn


def _session_with_call(tmp_path: Path, tool_name: str, call_id: str) -> Session:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path)
    session.append(UserMessage(content="go"))
    session.append(
        AssistantMessage(
            tool_calls=[ToolCall(id=call_id, name=tool_name, arguments={})],
            stop_reason="tool_use",
        )
    )
    return session


def _store_with_pending(
    session: Session, tool_name: str, call_id: str, *, replay_safe: bool
) -> Store:
    store = Store()
    operation = Operation.accept("op1", store, session_id=session.id)
    operation.begin_effect(
        PendingEffect(
            tool_name=tool_name, call_id=call_id, arguments={}, replay_safe=replay_safe
        ),
        turn=1,
    )
    return store


def _tool_results(messages: list) -> list[ToolResultMessage]:
    return [m for m in messages if isinstance(m, ToolResultMessage)]


async def test_pending_recovery_detects_interrupted_effect(tmp_path: Path) -> None:
    session = _session_with_call(tmp_path, "rt", "c1")
    store = _store_with_pending(session, "rt", "c1", replay_safe=True)
    agent = Agent(MODEL, stream_fn=_final("done"), tools=[], store=store, session=session)

    assert agent.pending_recovery()
    assert agent.pending_recovery()[0].operation_id == "op1"


async def test_recover_reruns_replay_safe_effect_and_continues(tmp_path: Path) -> None:
    session = _session_with_call(tmp_path, "rt", "c1")
    store = _store_with_pending(session, "rt", "c1", replay_safe=True)
    tool = ReplayTool()
    agent = Agent(
        MODEL, stream_fn=_final("done"), tools=[tool], store=store, session=session
    )

    assert await agent.recover() is True

    assert tool.calls == 1
    results = _tool_results(agent.messages)
    assert len(results) == 1
    assert results[0].tool_call_id == "c1"
    assert results[0].text() == "replayed"
    # the run continued after the recovered effect
    last = agent.messages[-1]
    assert isinstance(last, AssistantMessage)
    assert last.text() == "done"
    # nothing left pending, operation settled completed
    assert Operation.unsettled(store) == []
    result = store.get_value(operation_result("op1"))
    assert result is not None and result["status"] == OperationStatus.COMPLETED.value
    # incremental persistence recorded the recovered result
    reloaded = SessionManager(tmp_path / "sessions").open(session.path)
    assert any(isinstance(m, ToolResultMessage) for m in reloaded.messages())


async def test_recover_does_not_rerun_non_replay_safe_effect(tmp_path: Path) -> None:
    session = _session_with_call(tmp_path, "ot", "c1")
    store = _store_with_pending(session, "ot", "c1", replay_safe=False)
    tool = OpaqueTool()
    agent = Agent(
        MODEL, stream_fn=_final("done"), tools=[tool], store=store, session=session
    )

    assert await agent.recover() is True

    assert tool.calls == 0
    results = _tool_results(agent.messages)
    assert len(results) == 1
    assert results[0].is_error is True
    assert "not replay-safe" in results[0].text()
    result = store.get_value(operation_result("op1"))
    assert result is not None and result["status"] == OperationStatus.FAILED.value
    # still continues so the model can react to the unknown effect
    last = agent.messages[-1]
    assert isinstance(last, AssistantMessage)
    assert last.text() == "done"


async def test_recover_settles_open_operation_as_aborted(tmp_path: Path) -> None:
    session = _session_with_call(tmp_path, "rt", "c1")
    store = Store()
    Operation.accept("op1", store, session_id=session.id)  # accepted, never settled
    agent = Agent(
        MODEL, stream_fn=_final("done"), tools=[], store=store, session=session
    )

    assert await agent.recover() is True

    result = store.get_value(operation_result("op1"))
    assert result is not None and result["status"] == OperationStatus.ABORTED.value
    # no effect to reconcile, so the run is not resumed
    assert agent.messages[-1].role == "assistant"
    assert agent.messages[-1].stop_reason == "tool_use"


async def test_recover_noop_without_store(tmp_path: Path) -> None:
    session = _session_with_call(tmp_path, "rt", "c1")
    agent = Agent(MODEL, stream_fn=_final("done"), tools=[], session=session)
    assert agent.pending_recovery() == []
    assert await agent.recover() is False


async def test_recover_settles_even_when_result_already_present(tmp_path: Path) -> None:
    session = _session_with_call(tmp_path, "rt", "c1")
    session.append(
        ToolResultMessage(
            tool_call_id="c1", tool_name="rt", content=[TextContent(text="already")]
        )
    )
    store = _store_with_pending(session, "rt", "c1", replay_safe=True)
    tool = ReplayTool()
    agent = Agent(
        MODEL, stream_fn=_final("done"), tools=[tool], store=store, session=session
    )

    assert await agent.recover() is True

    assert tool.calls == 0  # not re-run
    assert Operation.unsettled(store) == []
    # only the pre-existing result exists
    assert len(_tool_results(agent.messages)) == 1
