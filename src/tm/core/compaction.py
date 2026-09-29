"""Context compaction: summarize older messages to free up context space."""

from __future__ import annotations

import json

from tm.ai.providers.base import StreamOptions
from tm.ai.types import AssistantMessage, Context, Message, ToolResultMessage, UserMessage
from tm.core.loop import StreamFn
from tm.utils.abort import AbortSignal

SUMMARY_PREFIX = "Summary of earlier conversation:\n"

DEFAULT_INSTRUCTIONS = """Create a structured context checkpoint that another model uses to continue the work. Do not continue the conversation. Use this exact format:

## Goal
[What the user is trying to accomplish]

## Constraints & Preferences
- [Constraints, preferences, or requirements, or "(none)"]

## Progress
### Done
- [x] [Completed work]
### In Progress
- [ ] [Current work]
### Blocked
- [Blockers, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [What should happen next]

## Critical Context
- [Data, paths, or references needed to continue, or "(none)"]

Keep each section concise. Preserve exact file paths, function names, commands, and error messages."""

UPDATE_INSTRUCTIONS = """The messages above are NEW conversation messages to incorporate into the existing summary in <previous-summary>. Update that summary with the new information. Rules:
- PRESERVE existing information, decisions, and progress from the previous summary.
- ADD new progress, decisions, and context; move finished "In Progress" items to "Done".
- UPDATE "Next Steps" based on what was accomplished.
- PRESERVE exact file paths, function names, commands, and error messages.
Use the same structured format as the previous summary."""


def _message_chars(message: Message) -> int:
    if isinstance(message, UserMessage):
        return len(message.content) if isinstance(message.content, str) else 200
    if isinstance(message, AssistantMessage):
        chars = len(message.text())
        for call in message.tool_calls:
            chars += len(call.name) + len(json.dumps(call.arguments))
        return chars
    if isinstance(message, ToolResultMessage):
        return len(message.text())
    return 0


def estimate_tokens(messages: list[Message], system_prompt: str | None = None) -> int:
    """Rough token estimate (about four characters per token)."""
    chars = len(system_prompt) if system_prompt else 0
    for message in messages:
        chars += _message_chars(message) + 16
    return chars // 4


def estimate_context_tokens(
    messages: list[Message], system_prompt: str | None = None
) -> int:
    """Estimate context tokens using provider usage when available.

    Prefers the token count reported by the most recent successful assistant
    message (accurate for the prefix) plus an estimate of the messages after it,
    matching pi's ``estimateContextTokens``.
    """
    last_total = 0
    index = -1
    for i in range(len(messages) - 1, -1, -1):
        message = messages[i]
        if not isinstance(message, AssistantMessage) or message.usage is None:
            continue
        if message.stop_reason in ("error", "aborted"):
            continue
        usage = message.usage
        total = usage.total or (usage.input + usage.output + usage.cache_read)
        if total > 0:
            last_total = total
            index = i
            break
    if index == -1:
        return estimate_tokens(messages, system_prompt)
    trailing_chars = sum(_message_chars(m) + 16 for m in messages[index + 1 :])
    return last_total + trailing_chars // 4


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


def format_transcript(messages: list[Message], *, max_chars: int | None = None) -> str:
    text = "\n".join(_render(message) for message in messages)
    if max_chars is not None and len(text) > max_chars:
        # Keep the most recent part; the summarizer only needs the gist.
        text = "... (earlier part truncated)\n" + text[-max_chars:]
    return text


def _summary_char_budget(model) -> int | None:
    window = int(getattr(model, "context_window", 0) or 0)
    if window <= 0:
        return None
    # Roughly half the window in characters (~4 chars/token), leaving room for
    # the instruction and the generated summary.
    return max(2000, window * 2)


def extract_file_operations(messages: list[Message]) -> tuple[list[str], list[str]]:
    """Read-only and modified file paths touched by tool calls in ``messages``."""
    read: set[str] = set()
    modified: set[str] = set()
    for message in messages:
        if not isinstance(message, AssistantMessage):
            continue
        for call in message.tool_calls:
            arguments = call.arguments if isinstance(call.arguments, dict) else {}
            path = arguments.get("path")
            if not isinstance(path, str) or not path:
                continue
            if call.name == "read":
                read.add(path)
            elif call.name in ("write", "edit"):
                modified.add(path)
    return sorted(read - modified), sorted(modified)


def format_file_operations(read_files: list[str], modified_files: list[str]) -> str:
    sections: list[str] = []
    if read_files:
        sections.append("<read-files>\n" + "\n".join(read_files) + "\n</read-files>")
    if modified_files:
        sections.append(
            "<modified-files>\n" + "\n".join(modified_files) + "\n</modified-files>"
        )
    return "\n\n" + "\n\n".join(sections) if sections else ""


async def summarize_messages(
    stream_fn: StreamFn,
    model,
    messages: list[Message],
    instructions: str | None = None,
    signal: AbortSignal | None = None,
    previous_summary: str | None = None,
) -> str:
    transcript = format_transcript(messages, max_chars=_summary_char_budget(model))
    base = UPDATE_INSTRUCTIONS if previous_summary else DEFAULT_INSTRUCTIONS
    if instructions:
        base = f"{base}\n\nAdditional focus: {instructions}"
    head = (
        f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
        if previous_summary
        else ""
    )
    prompt = f"{head}<conversation>\n{transcript}\n</conversation>\n\n{base}"
    context = Context(
        system_prompt="You compact conversations into concise, factual summaries.",
        messages=[UserMessage(content=prompt)],
    )
    options = StreamOptions(signal=signal, max_tokens=2048)
    stream = stream_fn(model, context, options)
    final = await stream.result()
    summary = final.text().strip()
    if not summary:
        return ""
    read_files, modified_files = extract_file_operations(messages)
    return summary + format_file_operations(read_files, modified_files)


__all__ = [
    "DEFAULT_INSTRUCTIONS",
    "SUMMARY_PREFIX",
    "UPDATE_INSTRUCTIONS",
    "estimate_context_tokens",
    "estimate_tokens",
    "extract_file_operations",
    "format_file_operations",
    "format_transcript",
    "summarize_messages",
]
