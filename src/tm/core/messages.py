"""Keep the message history valid for tool-calling providers.

Providers require that every ``tool`` result follows the assistant message that
requested it (matching ``tool_call_id``), and that every requested tool call has
a result. Compaction, branching, or an interrupted turn can break this, so the
history is repaired before it is sent.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter

from tm.ai.types import AssistantMessage, Message, ToolCall, ToolResultMessage

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
#: Openers of a planning sentence. A looping model re-announces the same next
#: step with slight rewording ("Let me run.", "Let me do it."), so the lines are
#: not identical but the intent repeats.
_PLAN_PREFIXES = ("let me", "let's", "i'll", "让我", "我来", "我准备")
_PLANNING_LINES = 8
_NORMALIZED_LINE_REPEATS = 3


def _normalized_line(line: str) -> str:
    return re.sub(r"[\s!.,;:…]+", " ", line.strip().lower()).strip()


def is_looping_text(text: str) -> bool:
    """Whether assistant text is a degenerate repetition loop.

    Detects raw tool-call markup, the same line repeated several times, and a
    run of planning sentences that only rephrase the next step. Such text is
    persisted when a run is stopped, but sending it back makes the model
    continue the same loop on the next turn. Strip it from the request.
    """
    if not text:
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in _LOOP_MARKERS):
        return True
    lines = [line.strip() for line in text.splitlines() if len(line.strip()) >= 8]
    if lines:
        if Counter(lines).most_common(1)[0][1] >= 5:
            return True
        normalized = Counter(_normalized_line(line) for line in lines)
        if normalized.most_common(1)[0][1] >= _NORMALIZED_LINE_REPEATS:
            return True
    planning = sum(
        1
        for line in text.splitlines()
        if line.strip().lower().lstrip("-*0123456789. ").startswith(_PLAN_PREFIXES)
    )
    if planning >= _PLANNING_LINES:
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
    return _collapse_repeated_tool_groups(repaired)


def _tool_group_key(call: ToolCall, result: ToolResultMessage) -> str:
    arguments = json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha1(result.text().encode("utf-8", errors="replace")).hexdigest()
    return f"{call.name}|{arguments}|{digest}"


def _collapse_repeated_tool_groups(messages: list[Message]) -> list[Message]:
    """Collapse identical tool call/result groups that repeat at least 3 times.

    Two identical calls may be legitimate (a poll, a re-read); three or more
    with the same result cannot make progress. Keep the first group only.
    """
    results = {
        message.tool_call_id: message
        for message in messages
        if isinstance(message, ToolResultMessage)
    }
    counts = Counter(
        _tool_group_key(call, results[call.id])
        for message in messages
        if isinstance(message, AssistantMessage)
        for call in message.tool_calls
        if call.id in results
    )
    seen: set[str] = set()
    dropped: set[int] = set()
    collapsed: list[Message] = []
    for message in messages:
        if isinstance(message, ToolResultMessage):
            if id(message) not in dropped:
                collapsed.append(message)
            continue
        if isinstance(message, AssistantMessage) and message.tool_calls:
            kept: list[ToolCall] = []
            for call in message.tool_calls:
                result = results.get(call.id)
                if result is not None:
                    key = _tool_group_key(call, result)
                    if key in seen and counts[key] >= 3:
                        dropped.add(id(result))
                        continue
                    seen.add(key)
                kept.append(call)
            if len(kept) != len(message.tool_calls):
                message = message.model_copy(update={"tool_calls": kept})
            if not kept and not message.text():
                continue
        collapsed.append(message)
    return collapsed


def strip_loop_output(
    messages: list[Message], *, collapse_tool_groups: bool = True
) -> tuple[list[Message], list[Message]]:
    """Remove loop-produced output when a run stops for a loop.

    Assistant text that reads as a degenerate repetition is cleared in place, so
    the messages already queued for persistence are corrected as well; an
    assistant message left with neither text nor tool calls is dropped. Identical
    tool call/result groups collapse to their first occurrence, because repeating
    a call with the same arguments and the same result cannot make progress.

    Returns ``(kept, dropped)``. ``dropped`` holds the message objects that must
    also be discarded from the pending persistence queue.
    """
    dropped_ids: set[int] = set()

    for message in messages:
        if isinstance(message, AssistantMessage) and is_looping_text(message.text()):
            message.content = []
            if not message.tool_calls:
                dropped_ids.add(id(message))

    results = {
        message.tool_call_id: message
        for message in messages
        if isinstance(message, ToolResultMessage)
    }
    seen: set[str] = set()
    for message in messages:
        if (
            not collapse_tool_groups
            or not isinstance(message, AssistantMessage)
            or id(message) in dropped_ids
        ):
            continue
        kept_calls: list[ToolCall] = []
        for call in message.tool_calls:
            result = results.get(call.id)
            if result is None:
                kept_calls.append(call)
                continue
            key = _tool_group_key(call, result)
            if key in seen:
                dropped_ids.add(id(result))
                continue
            seen.add(key)
            kept_calls.append(call)
        if len(kept_calls) != len(message.tool_calls):
            message.tool_calls = kept_calls
        if not message.tool_calls and not message.content:
            dropped_ids.add(id(message))

    # Dropping a duplicate call orphans its result; dropping a loop message can
    # orphan results from calls it no longer carries.
    requested = {
        call.id
        for message in messages
        if id(message) not in dropped_ids and isinstance(message, AssistantMessage)
        for call in message.tool_calls
    }
    for message in messages:
        if isinstance(message, ToolResultMessage) and message.tool_call_id not in requested:
            dropped_ids.add(id(message))

    kept = [message for message in messages if id(message) not in dropped_ids]
    dropped = [message for message in messages if id(message) in dropped_ids]
    return kept, dropped


__all__ = ["is_looping_text", "repair_tool_messages", "strip_loop_output"]
