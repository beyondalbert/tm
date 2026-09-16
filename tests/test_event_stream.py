import asyncio

import pytest

from tm.ai.event_stream import EventStream
from tm.ai.types import AssistantMessage


async def test_push_iterate_and_result() -> None:
    stream: EventStream[str, int] = EventStream()

    async def produce() -> None:
        stream.push("a")
        stream.push("b")
        stream.end(42)

    task = asyncio.get_running_loop().create_task(produce())
    stream.attach_task(task)

    seen = [event async for event in stream]
    assert seen == ["a", "b"]
    assert await stream.result() == 42


async def test_end_with_error_surfaces_from_result() -> None:
    stream: EventStream[str, AssistantMessage] = EventStream()
    error = RuntimeError("boom")
    stream.end_with_error(error)

    assert [event async for event in stream] == []
    with pytest.raises(RuntimeError, match="boom"):
        await stream.result()


async def test_push_after_end_is_ignored() -> None:
    stream: EventStream[str, None] = EventStream()
    stream.end(None)
    stream.push("ignored")
    assert [event async for event in stream] == []
