"""An async event stream with an awaitable final result.

Port of pi-ai's ``EventStream``: producers push events, consumers iterate them
with ``async for``, and anyone can ``await stream.result()`` for the final value.
"""

from __future__ import annotations

import asyncio
from typing import Generic, TypeVar

TEvent = TypeVar("TEvent")
TResult = TypeVar("TResult")

_SENTINEL = object()


class EventStream(Generic[TEvent, TResult]):
    def __init__(self) -> None:
        self._queue: asyncio.Queue[object] = asyncio.Queue()
        self._result: asyncio.Future[TResult] = asyncio.get_running_loop().create_future()
        self._ended = False
        self._task: asyncio.Task[None] | None = None

    @property
    def ended(self) -> bool:
        return self._ended

    def push(self, event: TEvent) -> None:
        if self._ended:
            return
        self._queue.put_nowait(event)

    def end(self, result: TResult) -> None:
        if self._ended:
            return
        self._ended = True
        if not self._result.done():
            self._result.set_result(result)
        self._queue.put_nowait(_SENTINEL)

    def end_with_error(self, error: BaseException) -> None:
        if self._ended:
            return
        self._ended = True
        if not self._result.done():
            self._result.set_exception(error)
        self._queue.put_nowait(_SENTINEL)

    def attach_task(self, task: asyncio.Task[None]) -> None:
        self._task = task

    def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def __aiter__(self) -> EventStream[TEvent, TResult]:
        return self

    async def __anext__(self) -> TEvent:
        item = await self._queue.get()
        if item is _SENTINEL:
            raise StopAsyncIteration
        return item  # type: ignore[return-value]

    async def result(self) -> TResult:
        return await self._result
