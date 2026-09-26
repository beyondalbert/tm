"""A cooperative pause primitive for the agent loop.

Pausing stops the loop at the next safe boundary (before a turn or before
executing tools) until it is resumed; it is independent of abort.
"""

from __future__ import annotations

import asyncio


class PauseController:
    def __init__(self) -> None:
        self._resumed = asyncio.Event()
        self._resumed.set()
        self._paused = False

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True
        self._resumed.clear()

    def resume(self) -> None:
        self._paused = False
        self._resumed.set()

    async def wait_if_paused(self) -> None:
        if self._paused:
            await self._resumed.wait()


__all__ = ["PauseController"]
