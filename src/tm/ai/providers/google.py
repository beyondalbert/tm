"""Google Gemini provider using the official ``google-genai`` SDK.

The SDK is imported lazily so the rest of TM works without the optional
``google`` extra installed.
"""

from __future__ import annotations

import asyncio
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
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from tm.utils.abort import AbortSignal

_STOP_REASONS = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "stop",
    "RECITATION": "stop",
    "OTHER": "stop",
}


def _parts_for_content(content: str | list[Content]) -> list[dict]:
    if isinstance(content, str):
        return [{"text": content}]
    parts: list[dict] = []
    for block in content:
        if isinstance(block, TextContent):
            parts.append({"text": block.text})
        elif isinstance(block, ImageContent):
            parts.append(
                {"inline_data": {"mime_type": block.mime_type, "data": block.data}}
            )
    return parts


def messages_to_google(messages: list[Message]) -> list[dict]:
    contents: list[dict] = []
    pending_tool_results: list[dict] = []

    def flush() -> None:
        if pending_tool_results:
            contents.append({"role": "user", "parts": list(pending_tool_results)})
            pending_tool_results.clear()

    for message in messages:
        if isinstance(message, ToolResultMessage):
            pending_tool_results.append(
                {
                    "function_response": {
                        "name": message.tool_name,
                        "response": {"result": message.text(), "is_error": message.is_error},
                    }
                }
            )
            continue
        flush()
        if isinstance(message, UserMessage):
            contents.append({"role": "user", "parts": _parts_for_content(message.content)})
        elif isinstance(message, AssistantMessage):
            parts: list[dict] = []
            for block in message.content:
                if isinstance(block, TextContent):
                    parts.append({"text": block.text})
            for call in message.tool_calls:
                parts.append(
                    {"function_call": {"name": call.name, "args": call.arguments}}
                )
            contents.append({"role": "model", "parts": parts})
    flush()
    return contents


def _stop_reason(value: Any) -> str:
    name = getattr(value, "name", None) or str(value)
    return _STOP_REASONS.get(name.upper(), "stop")


class GoogleProvider(Provider):
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
                from google import genai
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError(
                    "The google-genai package is required: pip install the-machine[google]"
                ) from exc
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _build_config(self, model: Model, context: Context, options: StreamOptions) -> dict:
        config: dict[str, Any] = {"max_output_tokens": options.max_tokens or model.max_tokens}
        if context.system_prompt:
            config["system_instruction"] = context.system_prompt
        if options.temperature is not None:
            config["temperature"] = options.temperature
        if context.tools:
            config["tools"] = [
                {
                    "function_declarations": [
                        {
                            "name": spec.name,
                            "description": spec.description,
                            "parameters_json_schema": spec.parameters,
                        }
                        for spec in context.tools
                    ]
                }
            ]
        return config

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
        tool_calls: list[ToolCall] = []
        usage = Usage()
        stop_reason = "stop"

        def snapshot() -> AssistantMessage:
            content: list[Content] = []
            if thinking_buf:
                content.append(ThinkingContent(thinking=thinking_buf))
            if text_buf:
                content.append(TextContent(text=text_buf))
            return AssistantMessage(
                model=model.id, content=content, tool_calls=list(tool_calls)
            )

        try:
            client = self._get_client()
            raw = await client.aio.models.generate_content_stream(
                model=model.id,
                contents=messages_to_google(context.messages),
                config=self._build_config(model, context, options),
            )
            stream.push(StartEvent(partial=snapshot()))

            async for chunk in raw:
                if signal is not None and signal.aborted:
                    stop_reason = "aborted"
                    break
                meta = getattr(chunk, "usage_metadata", None)
                if meta is not None:
                    usage.input = getattr(meta, "prompt_token_count", 0) or 0
                    usage.output = getattr(meta, "candidates_token_count", 0) or 0
                    usage.total = getattr(meta, "total_token_count", 0) or 0

                candidates = getattr(chunk, "candidates", None) or []
                if not candidates:
                    continue
                candidate = candidates[0]
                content = getattr(candidate, "content", None)
                parts = getattr(content, "parts", None) or [] if content is not None else []
                for part in parts:
                    text = getattr(part, "text", None)
                    if text:
                        if getattr(part, "thought", False):
                            thinking_buf += text
                            stream.push(ThinkingDeltaEvent(partial=snapshot(), delta=text))
                        else:
                            text_buf += text
                            stream.push(TextDeltaEvent(partial=snapshot(), delta=text))
                    call = getattr(part, "function_call", None)
                    if call is not None and getattr(call, "name", None):
                        tool_call = ToolCall(
                            id=getattr(call, "id", None) or f"call_{len(tool_calls)}",
                            name=call.name,
                            arguments=dict(getattr(call, "args", None) or {}),
                        )
                        tool_calls.append(tool_call)
                        stream.push(ToolCallStartEvent(partial=snapshot()))
                        stream.push(ToolCallEndEvent(partial=snapshot(), tool_call=tool_call))

                finish = getattr(candidate, "finish_reason", None)
                if finish is not None:
                    stop_reason = _stop_reason(finish)

            if tool_calls:
                stop_reason = "tool_use"

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
        self._client = None


__all__ = ["GoogleProvider", "messages_to_google"]
