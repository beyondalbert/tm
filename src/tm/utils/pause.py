"""A cooperative pause primitive for the agent loop.

Pausing stops the loop at the next safe boundary (before a turn or before
executing tools) until it is resumed; it is independent of abort.
"""

from __future__ import annotations

import asyncio


class PauseController:
    def __init__(self) -> None:
        self._paused = False
        # Created on first wait: on Python 3.8 an asyncio.Event binds to the
        # event loop current at construction, and the agent may be built
        # before any loop is running.
        self._resumed: asyncio.Event | None = None

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True
        if self._resumed is not None:
            self._resumed.clear()

    def resume(self) -> None:
        self._paused = False
        if self._resumed is not None:
            self._resumed.set()

    async def wait_if_paused(self) -> None:
        if not self._paused:
            return
        if self._resumed is None:
            self._resumed = asyncio.Event()
        await self._resumed.wait()


__all__ = ["PauseController"]
