"""Token estimates and request sizing.

The estimates are deliberately rough. They are used to decide when to compact
and to leave enough room for the reply; provider-reported usage is preferred
whenever an applicable assistant response is available.
"""

from __future__ import annotations

import json

from tm.ai.types import (
    AssistantMessage,
    Context,
    Message,
    Model,
    ToolResultMessage,
    ToolSpec,
    Usage,
    UserMessage,
)

#: Average characters per token used by the text estimate.
CHARS_PER_TOKEN = 4
#: Room kept free for provider-side counting differences and the chat template.
CONTEXT_SAFETY_TOKENS = 4096
#: Never ask for less output than this when fitting a request to the window.
MIN_MAX_TOKENS = 1024


def message_chars(message: Message) -> int:
    if isinstance(message, UserMessage):
        return len(message.content) if isinstance(message.content, str) else 200
    if isinstance(message, AssistantMessage):
        chars = len(message.text()) + len(message.thinking())
        for call in message.tool_calls:
            chars += len(call.name) + len(json.dumps(call.arguments))
        return chars
    if isinstance(message, ToolResultMessage):
        return len(message.text())
    return 0


def estimate_message_tokens(message: Message) -> int:
    """Estimated tokens for one message, including per-message overhead."""
    return message_chars(message) // CHARS_PER_TOKEN + 4


def estimate_tokens(
    messages: list[Message], system_prompt: str | None = None
) -> int:
    """Rough token estimate (about four characters per token)."""
    chars = len(system_prompt) if system_prompt else 0
    for message in messages:
        chars += message_chars(message) + 16
    return chars // CHARS_PER_TOKEN


def context_tokens(usage: Usage) -> int:
    """Total tokens a request occupied: prompt (cached or not) plus output."""
    return usage.total or (
        usage.input + usage.output + usage.cache_read + usage.cache_write
    )


def estimate_context_tokens(
    messages: list[Message], system_prompt: str | None = None
) -> int:
    """Estimate context tokens using provider usage when available.

    Prefers the token count reported by the most recent successful assistant
    message (accurate for the prefix) plus an estimate of the messages after it.
    Usage is ignored when a newer message was inserted before that response in
    the projection, such as a compaction summary: that count then describes a
    prefix the current request no longer sends.
    """
    last_total = 0
    index = -1
    latest_prefix_timestamp = 0
    for i, message in enumerate(messages):
        if isinstance(message, AssistantMessage):
            usage = message.usage
            applies = message.timestamp >= latest_prefix_timestamp
            if applies and usage is not None and message.stop_reason not in ("error", "aborted"):
                total = context_tokens(usage)
                if total > 0:
                    last_total = total
                    index = i
        latest_prefix_timestamp = max(latest_prefix_timestamp, message.timestamp)
    if index == -1:
        return estimate_tokens(messages, system_prompt)
    trailing = sum(estimate_message_tokens(m) for m in messages[index + 1 :])
    return last_total + trailing


def estimate_tools_tokens(tools: list[ToolSpec]) -> int:
    if not tools:
        return 0
    payload = [tool.model_dump(mode="json") for tool in tools]
    return len(json.dumps(payload, ensure_ascii=False)) // CHARS_PER_TOKEN


def estimate_request_tokens(context: Context) -> int:
    """Estimated prompt tokens for one request, including tool definitions.

    The usage anchor already includes the tools sent with that request, so this
    can overcount slightly. That is the safe direction for clamping output.
    """
    return estimate_context_tokens(
        context.messages, context.system_prompt
    ) + estimate_tools_tokens(context.tools)


def clamp_max_tokens(
    model: Model, context: Context, requested: int | None
) -> int:
    """Fit the output limit to the room the prompt leaves in the window."""
    max_tokens = requested if requested is not None and requested > 0 else model.max_tokens
    if max_tokens <= 0:
        max_tokens = MIN_MAX_TOKENS
    if model.context_window <= 0:
        return max_tokens
    available = model.context_window - estimate_request_tokens(context) - CONTEXT_SAFETY_TOKENS
    return min(max_tokens, max(MIN_MAX_TOKENS, available))


__all__ = [
    "CHARS_PER_TOKEN",
    "CONTEXT_SAFETY_TOKENS",
    "MIN_MAX_TOKENS",
    "clamp_max_tokens",
    "context_tokens",
    "estimate_context_tokens",
    "estimate_message_tokens",
    "estimate_request_tokens",
    "estimate_tokens",
    "estimate_tools_tokens",
    "message_chars",
]
