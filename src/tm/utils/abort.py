from __future__ import annotations

import asyncio


class AbortError(Exception):
    """Raised when an operation is cancelled through an AbortSignal."""


class AbortSignal:
    """Minimal cancellation primitive shared across the agent runtime."""

    def __init__(self) -> None:
        self._aborted = False
        # Created on first wait: on Python 3.8 an asyncio.Event binds to the
        # event loop current at construction, and a signal may be created
        # before any loop is running.
        self._event: asyncio.Event | None = None

    @property
    def aborted(self) -> bool:
        return self._aborted

    def abort(self) -> None:
        self._aborted = True
        if self._event is not None:
            self._event.set()

    async def wait(self) -> None:
        if self._aborted:
            return
        if self._event is None:
            self._event = asyncio.Event()
        await self._event.wait()

    def raise_if_aborted(self) -> None:
        if self.aborted:
            raise AbortError()
