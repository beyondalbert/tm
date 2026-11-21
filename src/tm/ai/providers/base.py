from __future__ import annotations

from dataclasses import dataclass

from tm.ai.event_stream import EventStream
from tm.ai.types import AssistantMessage, AssistantMessageEvent, Context, Model
from tm.utils.abort import AbortSignal

#: Thinking levels accepted by ``StreamOptions.thinking``. "auto" leaves the
#: provider default untouched; "off" asks the provider to stop reasoning.
THINKING_LEVELS = ("auto", "off", "low", "medium", "high")


@dataclass
class StreamOptions:
    temperature: float | None = None
    max_tokens: int | None = None
    #: Thinking level: one of ``THINKING_LEVELS``, or None for the provider default.
    thinking: str | None = None
    #: OpenAI-style tool choice: "auto", "none", "required", or None for auto.
    tool_choice: str | None = None
    signal: AbortSignal | None = None
    #: Provider prompt-cache retention: "short" (default) or "long".
    cache_retention: str | None = None


class Provider:
    """Base class for LLM providers."""

    def __init__(
        self,
        id: str,
        name: str,
        models: list[Model],
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        connect_timeout: float = 10.0,
    ) -> None:
        self.id = id
        self.name = name
        self._models = models
        self.api_key = api_key
        self.base_url = base_url
        #: Seconds allowed for one request; ``None`` means no limit.
        self.request_timeout = timeout
        self.connect_timeout = connect_timeout

    def list_models(self) -> list[Model]:
        return list(self._models)

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> EventStream[AssistantMessageEvent, AssistantMessage]:
        raise NotImplementedError

    def timeout_or_none(self) -> float | None:
        """The request timeout in seconds, or None when it is disabled."""
        if self.request_timeout is None or self.request_timeout <= 0:
            return None
        return float(self.request_timeout)

    async def aclose(self) -> None:
        return None


__all__ = ["THINKING_LEVELS", "Provider", "StreamOptions"]
