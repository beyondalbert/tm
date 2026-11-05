"""Keep the message history valid for tool-calling providers.

Providers require that every ``tool`` result follows the assistant message that
requested it (matching ``tool_call_id``), and that every requested tool call has
a result. Compaction, branching, or an interrupted turn can break this, so the
history is repaired before it is sent.
"""

from __future__ import annotations

from collections import Counter

from tm.ai.types import AssistantMessage, Message, ToolResultMessage

_LOOP_MARKERS = (
    "</parameter>",
    "<tool_call>",
    "</tool_call>",
    "<|tool_call",
    "<function_call>",
    "</invoke>",
)
_LOOP_PHRASES = (
    "dispatching",
    "emitting now",
    "ok sending",
    "让我发出",
    "现在发出",
    "发出。",
)


def is_looping_text(text: str) -> bool:
    """Whether assistant text is a degenerate tool-call/repetition loop.

    Such text is persisted when a run is stopped, but sending it back makes the
    model continue the same loop on the next turn. Strip it from the request.
    """
    if not text:
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in _LOOP_MARKERS):
        return True
    lines = [line.strip() for line in text.splitlines() if len(line.strip()) >= 8]
    if lines and Counter(lines).most_common(1)[0][1] >= 5:
        return True
    return sum(lowered.count(marker) for marker in _LOOP_PHRASES) >= 6


def repair_tool_messages(messages: list[Message]) -> list[Message]:
    """Drop orphan tool results and trim tool calls that have no result."""
    requested = {
        call.id
        for message in messages
        if isinstance(message, AssistantMessage)
        for call in message.tool_calls
    }
    answered = {
        message.tool_call_id
        for message in messages
        if isinstance(message, ToolResultMessage)
    }
    keep = requested & answered

    repaired: list[Message] = []
    for message in messages:
        if isinstance(message, ToolResultMessage):
            if message.tool_call_id in requested:
                repaired.append(message)
            continue
        if isinstance(message, AssistantMessage):
            if is_looping_text(message.text()):
                # Keep the tool calls (and their results) but remove the
                # self-reinforcing loop text from the model's context.
                message = message.model_copy(update={"content": []})
            if message.tool_calls:
                remaining = [call for call in message.tool_calls if call.id in keep]
                if len(remaining) != len(message.tool_calls):
                    message = message.model_copy(update={"tool_calls": remaining})
            if not message.tool_calls and not message.text():
                # An assistant message must have content or tool_calls; a
                # trimmed tool call, or a persisted error/abort turn, can leave
                # it empty. Drop it.
                continue
        repaired.append(message)
    return repaired


__all__ = ["is_looping_text", "repair_tool_messages"]
