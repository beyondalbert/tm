"""OpenAI Chat Completions compatible provider.

Covers OpenAI, DeepSeek, Qwen (DashScope compatible mode), Moonshot/Kimi,
Zhipu GLM, Ollama, vLLM, LM Studio, and any other OpenAI-compatible endpoint.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

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

_FINISH_REASONS = {
    "stop": "stop",
    "length": "length",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "stop",
}


def _parse_arguments(raw: str) -> dict:
    raw = raw.strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                value = json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return {}
        else:
            return {}
    return value if isinstance(value, dict) else {"value": value}


def _content_to_openai(content: str | list) -> Any:
    if isinstance(content, str):
        return content
    parts: list[dict] = []
    for block in content:
        if isinstance(block, TextContent):
            parts.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageContent):
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{block.mime_type};base64,{block.data}"},
                }
            )
    return parts


def message_to_openai(message: Message) -> dict:
    if isinstance(message, UserMessage):
        return {"role": "user", "content": _content_to_openai(message.content)}
    if isinstance(message, AssistantMessage):
        text = message.text()
        payload: dict[str, Any] = {"role": "assistant", "content": text or None}
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in message.tool_calls
            ]
        return payload
    if isinstance(message, ToolResultMessage):
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.text(),
        }
    raise TypeError(f"Unsupported message: {message!r}")


class OpenAICompatProvider(Provider):
    def __init__(
        self,
        id: str,
        name: str,
        models: list[Model],
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        include_usage: bool = True,
        client: AsyncOpenAI | None = None,
    ) -> None:
        super().__init__(id, name, models, api_key=api_key, base_url=base_url)
        self.include_usage = include_usage
        self._client = client

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(api_key=self.api_key or "not-needed", base_url=self.base_url)
        return self._client

    def _build_params(
        self, model: Model, context: Context, options: StreamOptions
    ) -> dict[str, Any]:
        messages: list[dict] = []
        if context.system_prompt:
            messages.append({"role": "system", "content": context.system_prompt})
        messages.extend(message_to_openai(m) for m in context.messages)

        params: dict[str, Any] = {
            "model": model.id,
            "messages": messages,
            "stream": True,
        }
        if options.temperature is not None:
            params["temperature"] = options.temperature
        params["max_tokens"] = options.max_tokens or model.max_tokens
        if context.tools:
            params["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.parameters,
                    },
                }
                for spec in context.tools
            ]
            params["tool_choice"] = "auto"
        if self.include_usage:
            params["stream_options"] = {"include_usage": True}
        return params

    @staticmethod
    def _parse_usage(raw: Any) -> Usage:
        details = getattr(raw, "prompt_tokens_details", None)
        cache_read = getattr(details, "cached_tokens", 0) or 0
        return Usage(
            input=getattr(raw, "prompt_tokens", 0) or 0,
            output=getattr(raw, "completion_tokens", 0) or 0,
            cache_read=cache_read,
            total=getattr(raw, "total_tokens", 0) or 0,
        )

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

    async def _open_stream(self, params: dict[str, Any]) -> AsyncIterator[Any]:
        client = self._get_client()
        raw = await client.chat.completions.create(**params)
        return raw

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
        usage: Usage | None = None
        stop_reason: str = "stop"

        def snapshot() -> AssistantMessage:
            content: list[Content] = []
            if thinking_buf:
                content.append(ThinkingContent(thinking=thinking_buf))
            if text_buf:
                content.append(TextContent(text=text_buf))
            return AssistantMessage(
                model=model.id,
                content=content,
                tool_calls=list(tool_calls.values()),
            )

        try:
            raw = await self._open_stream(self._build_params(model, context, options))
            stream.push(StartEvent(partial=snapshot()))

            async for chunk in raw:
                if signal is not None and signal.aborted:
                    stop_reason = "aborted"
                    break
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage = self._parse_usage(chunk_usage)

                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                if delta is not None:
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning:
                        thinking_buf += reasoning
                        stream.push(ThinkingDeltaEvent(partial=snapshot(), delta=reasoning))

                    content = getattr(delta, "content", None)
                    if content:
                        text_buf += content
                        stream.push(TextDeltaEvent(partial=snapshot(), delta=content))

                    for tc in getattr(delta, "tool_calls", None) or []:
                        idx = tc.index if tc.index is not None else 0
                        entry = tool_calls.get(idx)
                        if entry is None:
                            entry = ToolCall(id=tc.id or "", name="")
                            tool_calls[idx] = entry
                            tool_args[idx] = ""
                            stream.push(ToolCallStartEvent(partial=snapshot(), content_index=idx))
                        if tc.id:
                            entry.id = tc.id
                        fn = getattr(tc, "function", None)
                        if fn is not None:
                            if fn.name:
                                entry.name = fn.name
                            if fn.arguments:
                                tool_args[idx] += fn.arguments
                                stream.push(
                                    ToolCallDeltaEvent(
                                        partial=snapshot(), content_index=idx, delta=fn.arguments
                                    )
                                )

                finish = getattr(choice, "finish_reason", None)
                if finish:
                    stop_reason = _FINISH_REASONS.get(finish, "stop")

            for idx, entry in tool_calls.items():
                entry.arguments = _parse_arguments(tool_args.get(idx, ""))
                stream.push(ToolCallEndEvent(partial=snapshot(), tool_call=entry))

            if stop_reason == "tool_use" and not tool_calls:
                stop_reason = "stop"

            final = snapshot()
            final.stop_reason = stop_reason  # type: ignore[assignment]
            final.usage = usage
            stream.push(DoneEvent(partial=final, message=final))
            stream.end(final)
        except Exception as exc:  # noqa: BLE001 - provider errors are encoded as events
            error_message = AssistantMessage(
                model=model.id,
                content=[],
                stop_reason="error",
                error_message=str(exc),
            )
            stream.push(ErrorEvent(error=str(exc), message=error_message, partial=error_message))
            stream.end(error_message)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


__all__ = ["OpenAICompatProvider", "message_to_openai"]
