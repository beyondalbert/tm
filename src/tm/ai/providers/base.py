from __future__ import annotations

from dataclasses import dataclass

from tm.ai.event_stream import EventStream
from tm.ai.types import AssistantMessage, AssistantMessageEvent, Context, Model
from tm.utils.abort import AbortSignal


@dataclass
class StreamOptions:
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning: str | None = None
    signal: AbortSignal | None = None


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
    ) -> None:
        self.id = id
        self.name = name
        self._models = models
        self.api_key = api_key
        self.base_url = base_url

    def list_models(self) -> list[Model]:
        return list(self._models)

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> EventStream[AssistantMessageEvent, AssistantMessage]:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


__all__ = ["Provider", "StreamOptions"]
