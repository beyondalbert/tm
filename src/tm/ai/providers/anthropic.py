"""Anthropic Messages API provider.

Uses the official ``anthropic`` SDK, imported lazily so the rest of TM works
without the optional dependency installed.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from tm.ai.event_stream import EventStream
from tm.ai.providers.base import Provider, StreamOptions
from tm.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Content,
    Context,
    DoneEvent,
    ErrorEvent,
    ImageContent,
    Message,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ToolCall,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from tm.utils.abort import AbortSignal

_STOP_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_use",
}


def _parse_arguments(raw: str) -> dict:
    raw = raw.strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {"value": value}


def _blocks_to_anthropic(content: str | list[Content]) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    blocks: list[dict] = []
    for block in content:
        if isinstance(block, TextContent):
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageContent):
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": block.mime_type,
                        "data": block.data,
                    },
                }
            )
    return blocks


def messages_to_anthropic(messages: list[Message]) -> list[dict]:
    result: list[dict] = []
    pending_tool_results: list[dict] = []

    def flush_tool_results() -> None:
        if pending_tool_results:
            result.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for message in messages:
        if isinstance(message, ToolResultMessage):
            pending_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.text(),
                    "is_error": message.is_error,
                }
            )
            continue
        flush_tool_results()
        if isinstance(message, UserMessage):
            result.append({"role": "user", "content": _blocks_to_anthropic(message.content)})
        elif isinstance(message, AssistantMessage):
            blocks: list[dict] = []
            for block in message.content:
                if isinstance(block, TextContent):
                    blocks.append({"type": "text", "text": block.text})
            for call in message.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
            result.append({"role": "assistant", "content": blocks})
    flush_tool_results()
    return result


class AnthropicProvider(Provider):
    def __init__(
        self,
        id: str,
        name: str,
        models: list[Model],
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client: Any = None,
    ) -> None:
        super().__init__(id, name, models, api_key=api_key, base_url=base_url)
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError(
                    "The anthropic package is required: pip install the-machine[anthropic]"
                ) from exc

            kwargs: dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = AsyncAnthropic(**kwargs)
        return self._client

    def _build_params(
        self, model: Model, context: Context, options: StreamOptions
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model.id,
            "max_tokens": options.max_tokens or model.max_tokens,
            "messages": messages_to_anthropic(context.messages),
            "stream": True,
        }
        if context.system_prompt:
            params["system"] = context.system_prompt
        if options.temperature is not None:
            params["temperature"] = options.temperature
        if context.tools:
            params["tools"] = [
                {
                    "name": spec.name,
                    "description": spec.description,
                    "input_schema": spec.parameters,
                }
                for spec in context.tools
            ]
        return params

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> EventStream[AssistantMessageEvent, AssistantMessage]:
        opts = options or StreamOptions()
        stream: EventStream[AssistantMessageEvent, AssistantMessage] = EventStream()
        task = asyncio.get_running_loop().create_task(self._run(stream, model, context, opts))
        stream.attach_task(task)
        return stream

    async def _run(
        self,
        stream: EventStream[AssistantMessageEvent, AssistantMessage],
        model: Model,
        context: Context,
        options: StreamOptions,
    ) -> None:
        signal: AbortSignal | None = options.signal
        text_buf = ""
        thinking_buf = ""
        tool_calls: dict[int, ToolCall] = {}
        tool_args: dict[int, str] = {}
        usage = Usage()
        stop_reason = "stop"

        def snapshot() -> AssistantMessage:
            content: list[Content] = []
            if thinking_buf:
                content.append(ThinkingContent(thinking=thinking_buf))
            if text_buf:
                content.append(TextContent(text=text_buf))
            return AssistantMessage(
                model=model.id, content=content, tool_calls=list(tool_calls.values())
            )

        try:
            client = self._get_client()
            raw = await client.messages.create(**self._build_params(model, context, options))
            stream.push(StartEvent(partial=snapshot()))

            async for event in raw:
                if signal is not None and signal.aborted:
                    stop_reason = "aborted"
                    break
                event_type = getattr(event, "type", None)
                if event_type == "message_start":
                    message = getattr(event, "message", None)
                    input_usage = getattr(message, "usage", None)
                    if input_usage is not None:
                        usage.input = getattr(input_usage, "input_tokens", 0) or 0
                elif event_type == "content_block_start":
                    block = getattr(event, "content_block", None)
                    if getattr(block, "type", None) == "tool_use":
                        index = getattr(event, "index", 0)
                        tool_calls[index] = ToolCall(
                            id=getattr(block, "id", "") or "", name=getattr(block, "name", "") or ""
                        )
                        tool_args[index] = ""
                        stream.push(ToolCallStartEvent(partial=snapshot(), content_index=index))
                elif event_type == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    delta_type = getattr(delta, "type", None)
                    if delta_type == "text_delta":
                        text_buf += getattr(delta, "text", "") or ""
                        stream.push(
                            TextDeltaEvent(partial=snapshot(), delta=getattr(delta, "text", "") or "")
                        )
                    elif delta_type == "thinking_delta":
                        piece = getattr(delta, "thinking", "") or ""
                        thinking_buf += piece
                        stream.push(ThinkingDeltaEvent(partial=snapshot(), delta=piece))
                    elif delta_type == "input_json_delta":
                        index = getattr(event, "index", 0)
                        piece = getattr(delta, "partial_json", "") or ""
                        tool_args[index] = tool_args.get(index, "") + piece
                        stream.push(
                            ToolCallDeltaEvent(
                                partial=snapshot(), content_index=index, delta=piece
                            )
                        )
                elif event_type == "message_delta":
                    delta = getattr(event, "delta", None)
                    reason = getattr(delta, "stop_reason", None)
                    if reason:
                        stop_reason = _STOP_REASONS.get(reason, "stop")
                    out_usage = getattr(event, "usage", None)
                    if out_usage is not None:
                        usage.output = getattr(out_usage, "output_tokens", 0) or 0

            for index, call in tool_calls.items():
                call.arguments = _parse_arguments(tool_args.get(index, ""))
                stream.push(ToolCallEndEvent(partial=snapshot(), tool_call=call))

            usage.total = usage.input + usage.output
            final = snapshot()
            final.stop_reason = stop_reason  # type: ignore[assignment]
            final.usage = usage
            stream.push(DoneEvent(partial=final, message=final))
            stream.end(final)
        except Exception as exc:  # noqa: BLE001 - provider errors are encoded as events
            error_message = AssistantMessage(
                model=model.id, content=[], stop_reason="error", error_message=str(exc)
            )
            stream.push(ErrorEvent(error=str(exc), message=error_message, partial=error_message))
            stream.end(error_message)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


__all__ = ["AnthropicProvider", "messages_to_anthropic"]
