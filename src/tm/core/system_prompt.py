"""Default system prompt builder."""

from __future__ import annotations

from pathlib import Path


def build_system_prompt(cwd: Path, extra: str | None = None) -> str:
    parts = [
        "You are TM (The Machine), a local agent that can read and modify files "
        "and run shell commands on the user's machine.",
        f"Current working directory: {cwd}",
        "Use the provided tools to accomplish the request. Read files before "
        "editing them. After running a command, check its output before "
        "concluding. Keep responses concise and technical.",
    ]
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


__all__ = ["build_system_prompt"]
