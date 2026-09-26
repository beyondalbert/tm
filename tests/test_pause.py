from __future__ import annotations

import asyncio

from tm.ai.event_stream import EventStream
from tm.ai.types import AssistantMessage, DoneEvent, Model, StartEvent, TextContent
from tm.core.agent import Agent
from tm.utils.pause import PauseController

MODEL = Model(id="fake", provider="fake")


def make_stream(message: AssistantMessage) -> EventStream:
    stream: EventStream = EventStream()
    stream.push(StartEvent(partial=message))
    stream.push(DoneEvent(partial=message, message=message))
    stream.end(message)
    return stream


async def test_pause_controller_blocks_until_resumed() -> None:
    pause = PauseController()
    pause.pause()
    waiter = asyncio.create_task(pause.wait_if_paused())
    await asyncio.sleep(0.01)
    assert waiter.done() is False
    pause.resume()
    await asyncio.wait_for(waiter, timeout=1)
    assert pause.paused is False


async def test_agent_does_not_call_the_model_while_paused() -> None:
    calls = {"n": 0}

    def stream_fn(model, context, options) -> EventStream:
        calls["n"] += 1
        return make_stream(
            AssistantMessage(content=[TextContent(text="done")], stop_reason="stop")
        )

    agent = Agent(MODEL, stream_fn=stream_fn)
    agent.pause.pause()

    task = asyncio.create_task(agent.prompt("go"))
    await asyncio.sleep(0.05)
    assert calls["n"] == 0

    agent.pause.resume()
    await asyncio.wait_for(task, timeout=2)
    assert calls["n"] == 1
    assert agent.last_stop_reason == "stop"


async def test_abort_wakes_a_paused_agent() -> None:
    calls = {"n": 0}

    def stream_fn(model, context, options) -> EventStream:
        calls["n"] += 1
        return make_stream(
            AssistantMessage(content=[TextContent(text="done")], stop_reason="stop")
        )

    agent = Agent(MODEL, stream_fn=stream_fn)
    agent.pause.pause()

    task = asyncio.create_task(agent.prompt("go"))
    await asyncio.sleep(0.05)
    assert calls["n"] == 0

    agent.abort()
    await asyncio.wait_for(task, timeout=2)
    assert calls["n"] == 0
    assert agent.last_stop_reason == "aborted"


def test_abort_clears_pause() -> None:
    agent = Agent(
        MODEL,
        stream_fn=lambda model, context, options: make_stream(
            AssistantMessage(content=[TextContent(text="x")], stop_reason="stop")
        ),
    )
    agent.pause.pause()
    agent.abort()
    assert agent.pause.paused is False


async def test_abort_cancels_a_stalled_stream() -> None:
    from tm.ai.event_stream import EventStream

    stalled: EventStream = EventStream()

    def stream_fn(model, context, options) -> EventStream:
        return stalled

    agent = Agent(MODEL, stream_fn=stream_fn)
    task = asyncio.create_task(agent.prompt("go"))
    await asyncio.sleep(0.05)
    assert task.done() is False

    agent.abort()
    await asyncio.wait_for(task, timeout=2)
    assert agent.last_stop_reason == "aborted"
