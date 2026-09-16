from __future__ import annotations

import asyncio


class AbortError(Exception):
    """Raised when an operation is cancelled through an AbortSignal."""


class AbortSignal:
    """Minimal cancellation primitive shared across the agent runtime."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def aborted(self) -> bool:
        return self._event.is_set()

    def abort(self) -> None:
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()

    def raise_if_aborted(self) -> None:
        if self.aborted:
            raise AbortError()
