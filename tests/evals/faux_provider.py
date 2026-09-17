"""A deterministic faux provider for evals and tests.

Adapted from Pi's ``faux provider`` used by its coding-agent test suite. No real
API key, network call, or paid token is ever needed: the provider replays a
scripted list of assistant messages, so eval scenarios are fast, offline, and
reproducible.

Usage::

    provider = FauxProvider(script=[
        AssistantMessage(tool_calls=[ToolCall(id="1", name="echo", ...)], stop_reason="tool_use"),
        AssistantMessage(content=[TextContent(text="done")], stop_reason="stop"),
    ])
"""

from __future__ import annotations

from tm.ai.event_stream import EventStream
from tm.ai.providers.base import Provider, StreamOptions
from tm.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    DoneEvent,
    Model,
    StartEvent,
    TextDeltaEvent,
)

FAUX_MODEL = Model(id="faux-model", provider="faux", context_window=32_000)


class FauxProvider(Provider):
    """Replays a scripted sequence of assistant messages, one per request."""

    def __init__(self, script: list[AssistantMessage]) -> None:
        super().__init__("faux", "Faux", [FAUX_MODEL])
        self._script = list(script)
        self._index = 0
        self.requests: list[Context] = []

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> EventStream[AssistantMessageEvent, AssistantMessage]:
        self.requests.append(context)
        stream: EventStream[AssistantMessageEvent, AssistantMessage] = EventStream()
        if self._index >= len(self._script):
            # Exhausted script: return a clean stop so loops terminate.
            message = AssistantMessage(stop_reason="stop")
        else:
            message = self._script[self._index]
            self._index += 1

        stream.push(StartEvent(partial=message))
        if message.text():
            stream.push(TextDeltaEvent(partial=message, delta=message.text()))
        stream.push(DoneEvent(partial=message, message=message))
        stream.end(message)
        return stream


__all__ = ["FAUX_MODEL", "FauxProvider"]
