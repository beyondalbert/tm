"""Context compaction: summarize older messages to free up context space."""

from __future__ import annotations

import re

from tm.ai.estimate import (
    context_tokens,
    estimate_context_tokens,
    estimate_message_tokens,
    estimate_tokens,
)
from tm.ai.providers.base import StreamOptions
from tm.ai.types import (
    AssistantMessage,
    Context,
    Message,
    ToolResultMessage,
    UserMessage,
)
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


def token_cut_index(messages: list[Message], keep_tokens: int) -> int:
    """Index of the oldest message to keep for a trailing token budget."""
    if keep_tokens <= 0:
        return len(messages)
    total = 0
    index = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        total += estimate_message_tokens(messages[i])
        index = i
        if total >= keep_tokens:
            break
    return index


#: Errors that mean the provider rejected the request as too large.
_OVERFLOW_PATTERNS = (
    re.compile(r"prompt (?:is )?too long", re.I),
    re.compile(r"prompt exceeds max length", re.I),
    re.compile(r"request_too_large", re.I),
    re.compile(r"exceeds the context window", re.I),
    re.compile(r"maximum context length", re.I),
    re.compile(r"context window exceeded", re.I),
    re.compile(r"maximum prompt length", re.I),
    re.compile(r"reduce the length of the messages", re.I),
    re.compile(r"exceeds the maximum allowed input length", re.I),
    re.compile(r"exceeds the limit of \d+", re.I),
    re.compile(r"exceeds the available context size", re.I),
    re.compile(r"context window exceeds limit", re.I),
    re.compile(r"exceeded model token limit", re.I),
    re.compile(r"prompt has [\d,]+ tokens, but the configured context size", re.I),
    re.compile(r"context[_ ]length[_ ]exceeded", re.I),
    re.compile(r"^(?:[\w -]+ API error \(413\):|413(?:\s|:|$))", re.I),
)

#: Errors that mention tokens but are not context overflows.
_NON_OVERFLOW_PATTERNS = (
    re.compile(r"rate limit", re.I),
    re.compile(r"too many requests", re.I),
    re.compile(r"throttl", re.I),
)


def is_context_overflow(message: AssistantMessage, context_window: int = 0) -> bool:
    """Whether the response means the prompt did not fit the context window."""
    if message.stop_reason == "error" and message.error_message:
        text = message.error_message
        if any(pattern.search(text) for pattern in _NON_OVERFLOW_PATTERNS):
            return False
        if any(pattern.search(text) for pattern in _OVERFLOW_PATTERNS):
            return True
    usage = message.usage
    if usage is None or context_window <= 0:
        return False
    input_tokens = usage.input + usage.cache_read + usage.cache_write
    if message.stop_reason == "stop":
        return input_tokens > context_window
    if message.stop_reason == "length" and usage.output == 0:
        return input_tokens >= context_window * 0.99
    return False


def is_recoverable_length(
    message: AssistantMessage, desired_max_output: int
) -> bool:
    """Whether an early length stop may be retried after compaction."""
    if message.stop_reason != "length" or desired_max_output <= 0:
        return False
    return message.usage is None or message.usage.output < desired_max_output


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
    "context_tokens",
    "estimate_context_tokens",
    "estimate_tokens",
    "extract_file_operations",
    "format_file_operations",
    "format_transcript",
    "is_context_overflow",
    "is_recoverable_length",
    "summarize_messages",
    "token_cut_index",
]
