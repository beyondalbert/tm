"""Context compaction: summarize older messages to free up context space."""

from __future__ import annotations

import json

from tm.ai.providers.base import StreamOptions
from tm.ai.types import AssistantMessage, Context, Message, ToolResultMessage, UserMessage
from tm.core.loop import StreamFn

DEFAULT_INSTRUCTIONS = (
    "Summarize the conversation. Preserve decisions, file paths, commands run, "
    "results, and open tasks. Be concise and factual."
)


def estimate_tokens(messages: list[Message], system_prompt: str | None = None) -> int:
    """Rough token estimate (about four characters per token)."""
    chars = len(system_prompt) if system_prompt else 0
    for message in messages:
        if isinstance(message, UserMessage):
            chars += len(message.content) if isinstance(message.content, str) else 200
        elif isinstance(message, AssistantMessage):
            chars += len(message.text())
            for call in message.tool_calls:
                chars += len(call.name) + len(json.dumps(call.arguments))
        elif isinstance(message, ToolResultMessage):
            chars += len(message.text())
        chars += 16
    return chars // 4


def _render(message: Message, tool_limit: int = 500) -> str:
    if isinstance(message, UserMessage):
        content = message.content if isinstance(message.content, str) else "[multimodal]"
        return f"USER: {content}"
    if isinstance(message, AssistantMessage):
        parts: list[str] = []
        if message.text():
            parts.append(message.text())
        for call in message.tool_calls:
            parts.append(f"[calls {call.name} {call.arguments}]")
        return f"ASSISTANT: {' '.join(parts)}"
    if isinstance(message, ToolResultMessage):
        text = message.text()
        if len(text) > tool_limit:
            text = text[:tool_limit] + "..."
        return f"TOOL {message.tool_name}: {text}"
    return ""


def format_transcript(messages: list[Message]) -> str:
    return "\n".join(_render(message) for message in messages)


async def summarize_messages(
    stream_fn: StreamFn,
    model,
    messages: list[Message],
    instructions: str | None = None,
) -> str:
    prompt = (
        f"{instructions or DEFAULT_INSTRUCTIONS}\n\n"
        f"--- Conversation ---\n{format_transcript(messages)}"
    )
    context = Context(
        system_prompt="You compact conversations into concise, factual summaries.",
        messages=[UserMessage(content=prompt)],
    )
    stream = stream_fn(model, context, StreamOptions())
    final = await stream.result()
    return final.text().strip()


__all__ = [
    "DEFAULT_INSTRUCTIONS",
    "estimate_tokens",
    "format_transcript",
    "summarize_messages",
]
