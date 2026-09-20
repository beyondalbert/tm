"""Keep the message history valid for tool-calling providers.

Providers require that every ``tool`` result follows the assistant message that
requested it (matching ``tool_call_id``), and that every requested tool call has
a result. Compaction, branching, or an interrupted turn can break this, so the
history is repaired before it is sent.
"""

from __future__ import annotations

from tm.ai.types import AssistantMessage, Message, ToolResultMessage


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
        if isinstance(message, AssistantMessage) and message.tool_calls:
            remaining = [call for call in message.tool_calls if call.id in keep]
            if len(remaining) != len(message.tool_calls):
                message = message.model_copy(update={"tool_calls": remaining})
        repaired.append(message)
    return repaired


__all__ = ["repair_tool_messages"]
