from __future__ import annotations

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024


def truncate_text(
    text: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> tuple[str, bool]:
    truncated = False
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True
    result = "\n".join(lines)
    if len(result.encode("utf-8")) > max_bytes:
        result = result.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
        truncated = True
    return result, truncated


__all__ = ["DEFAULT_MAX_BYTES", "DEFAULT_MAX_LINES", "truncate_text"]
