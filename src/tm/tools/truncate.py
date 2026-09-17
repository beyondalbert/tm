"""Truncation for tool output.

Two strategies, matching pi's harness:

- :func:`truncate_head` keeps the beginning (file reads, search results).
- :func:`truncate_tail` keeps the end, where shell errors and final results are.

Limits are independent: whichever of the line or byte limit is hit first wins.
Whole lines are preferred; the only partial line is the last line of a tail
truncation whose single line exceeds the byte limit.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024
GREP_MAX_LINE_LENGTH = 500


@dataclass(frozen=True)
class Truncation:
    content: str
    truncated: bool
    truncated_by: str | None
    total_lines: int
    total_bytes: int
    output_lines: int
    output_bytes: int
    max_lines: int
    max_bytes: int
    last_line_partial: bool = False


def _byte_length(text: str) -> int:
    return len(text.encode("utf-8"))


def _split_lines(text: str) -> list[str]:
    if not text:
        return []
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return lines


def _result(
    content: str,
    *,
    truncated: bool,
    truncated_by: str | None,
    total_lines: int,
    total_bytes: int,
    max_lines: int,
    max_bytes: int,
    last_line_partial: bool = False,
) -> Truncation:
    return Truncation(
        content=content,
        truncated=truncated,
        truncated_by=truncated_by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        output_lines=len(content.split("\n")) if content else 0,
        output_bytes=_byte_length(content),
        max_lines=max_lines,
        max_bytes=max_bytes,
        last_line_partial=last_line_partial,
    )


def truncate_head(
    text: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Truncation:
    """Keep the first lines/bytes that fit."""
    total_bytes = _byte_length(text)
    lines = _split_lines(text)
    total_lines = len(lines)
    if total_lines <= max_lines and total_bytes <= max_bytes:
        return _result(
            text,
            truncated=False,
            truncated_by=None,
            total_lines=total_lines,
            total_bytes=total_bytes,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    kept: list[str] = []
    used = 0
    truncated_by = "lines"
    for index, line in enumerate(lines[:max_lines]):
        line_bytes = _byte_length(line) + (1 if index > 0 else 0)
        if used + line_bytes > max_bytes:
            truncated_by = "bytes"
            break
        kept.append(line)
        used += line_bytes
    if len(kept) >= max_lines and used <= max_bytes:
        truncated_by = "lines"

    return _result(
        "\n".join(kept),
        truncated=True,
        truncated_by=truncated_by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


def truncate_tail(
    text: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Truncation:
    """Keep the last lines/bytes that fit (errors and final output live here)."""
    total_bytes = _byte_length(text)
    lines = _split_lines(text)
    total_lines = len(lines)
    if total_lines <= max_lines and total_bytes <= max_bytes:
        return _result(
            text,
            truncated=False,
            truncated_by=None,
            total_lines=total_lines,
            total_bytes=total_bytes,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    kept: list[str] = []
    used = 0
    truncated_by = "lines"
    last_line_partial = False
    for line in reversed(lines):
        line_bytes = _byte_length(line) + (1 if kept else 0)
        if used + line_bytes > max_bytes:
            truncated_by = "bytes"
            if not kept:
                partial = _tail_bytes(line, max_bytes)
                kept.append(partial)
                used = _byte_length(partial)
                last_line_partial = True
            break
        kept.append(line)
        used += line_bytes
        if len(kept) >= max_lines:
            break
    if len(kept) >= max_lines and used <= max_bytes:
        truncated_by = "lines"

    kept.reverse()
    return _result(
        "\n".join(kept),
        truncated=True,
        truncated_by=truncated_by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        max_lines=max_lines,
        max_bytes=max_bytes,
        last_line_partial=last_line_partial,
    )


def _tail_bytes(text: str, max_bytes: int) -> str:
    """The longest suffix of ``text`` within ``max_bytes``, without splitting a codepoint."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    tail = encoded[-max_bytes:]
    while tail and (tail[0] & 0xC0) == 0x80:
        tail = tail[1:]
    return tail.decode("utf-8", errors="ignore")


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


def truncate_line(
    line: str, max_chars: int = GREP_MAX_LINE_LENGTH
) -> tuple[str, bool]:
    """Cap a single line, keeping the front (where a match usually starts)."""
    if len(line) <= max_chars:
        return line, False
    return f"{line[:max_chars]}... [truncated]", True


def truncate_text(
    text: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> tuple[str, bool]:
    """Backwards-compatible head truncation returning ``(content, truncated)``."""
    result = truncate_head(text, max_lines=max_lines, max_bytes=max_bytes)
    return result.content, result.truncated


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_LINES",
    "GREP_MAX_LINE_LENGTH",
    "Truncation",
    "format_size",
    "truncate_head",
    "truncate_line",
    "truncate_tail",
    "truncate_text",
]
