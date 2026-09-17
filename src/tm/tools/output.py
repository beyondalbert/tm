"""Spill full tool output to a file and format truncation notices.

When output is truncated, the complete text is written under
``<config>/workspace/spill`` so nothing is lost; the notice shown to the model
points at that file.
"""

from __future__ import annotations

import time
from pathlib import Path

from tm.config import config_dir
from tm.tools.truncate import Truncation, format_size


def spill_output(text: str, *, name: str = "output") -> Path | None:
    """Write the full ``text`` to a spill file. Returns the path, or None."""
    if not text:
        return None
    safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in name)
    safe = safe.strip("-")[:40] or "output"
    directory = config_dir() / "workspace" / "spill"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe}.log"
        path.write_text(text, encoding="utf-8", errors="replace")
    except OSError:
        return None
    return path


def format_truncation(
    result: Truncation, *, full_path: Path | None = None, tail: bool = True
) -> str:
    """A one-line notice explaining what was kept and where the rest is."""
    if not result.truncated:
        return ""
    if result.output_lines == 0:
        head = f"[First line exceeds {format_size(result.max_bytes)}"
    elif tail and result.last_line_partial:
        head = f"[Showing the end of line {result.total_lines}"
    elif tail:
        start = result.total_lines - result.output_lines + 1
        head = f"[Showing lines {start}-{result.total_lines} of {result.total_lines}"
    else:
        head = f"[Showing lines 1-{result.output_lines} of {result.total_lines}"
    if result.truncated_by == "bytes" and result.output_lines > 0:
        head += f" ({format_size(result.max_bytes)} limit)"
    if full_path is not None:
        return f"{head}. Full output: {full_path}]"
    return f"{head}]"


__all__ = ["format_truncation", "spill_output"]
