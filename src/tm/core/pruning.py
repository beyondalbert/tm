"""Shrink the request context by rewriting old tool output.

The session keeps every tool result verbatim; only the view sent to the model is
trimmed. Results older than the last ``keep_turns`` assistant turns and larger
than ``min_chars`` are replaced by a short stub pointing at a spill file with
the full text, so the model can ask for it again (or read the file) when needed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from tm.ai.types import AssistantMessage, Message, TextContent, ToolResultMessage
from tm.config import Settings, config_dir

#: Prefix of a rewritten result. Used to avoid pruning a stub that was already
#: rewritten on an earlier request.
PRUNE_PREFIX = "[Earlier "


@dataclass(frozen=True)
class PrunePolicy:
    """When and how old tool results are rewritten for the request."""

    enabled: bool = False
    #: Keep results that follow the last ``keep_turns`` assistant messages.
    keep_turns: int = 8
    #: Only rewrite results whose text is at least this many characters.
    min_chars: int = 4000
    #: Characters of the original kept in the stub (the tail, where errors live).
    keep_chars: int = 400
    #: Where full outputs are saved; None disables spilling.
    spill_dir: Path | None = None


def resolve_spill_dir(raw: str | None) -> Path:
    """The configured spill directory, or ``<config>/workspace/spill``."""
    text = (raw or "").strip()
    if text:
        return Path(text).expanduser()
    return config_dir() / "workspace" / "spill"


def policy_from_settings(settings: Settings) -> PrunePolicy:
    return PrunePolicy(
        enabled=bool(settings.prune_tool_output),
        keep_turns=int(settings.prune_tool_output_keep_turns),
        min_chars=int(settings.prune_tool_output_min_chars),
        keep_chars=int(settings.prune_tool_output_keep_chars),
        spill_dir=resolve_spill_dir(settings.spill_dir),
    )


def _cutoff(messages: list[Message], keep_turns: int) -> int:
    """Index before which tool results may be rewritten."""
    seen = 0
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], AssistantMessage):
            seen += 1
            if seen == keep_turns:
                return index
    return 0


def _spill(text: str, *, tool_name: str, directory: Path | None) -> Path | None:
    if directory is None:
        return None
    digest = hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in tool_name)
    safe = safe.strip("-")[:32] or "output"
    path = directory / f"prune-{digest}-{safe}.log"
    if path.exists():
        return path
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", errors="replace")
    except OSError:
        return None
    return path


def _stub(text: str, *, tool_name: str, spill: Path | None, keep_chars: int) -> str:
    where = f"Full output: {spill}." if spill is not None else "Full output was not saved."
    head = (
        f"{PRUNE_PREFIX}{tool_name} output omitted ({len(text):,} chars) to keep the "
        f"context small. {where} Re-run the tool if you need it again."
    )
    if keep_chars <= 0:
        return head
    tail = text[-keep_chars:]
    return f"{head}\n…{tail}"


def prune_tool_results(messages: list[Message], policy: PrunePolicy) -> list[Message]:
    """Return a request-only view with old, large tool results rewritten."""
    if not policy.enabled:
        return messages
    cutoff = _cutoff(messages, max(1, policy.keep_turns))
    if cutoff <= 0:
        return messages
    pruned: list[Message] = []
    changed = False
    for index, message in enumerate(messages):
        if (
            index >= cutoff
            or not isinstance(message, ToolResultMessage)
            or not isinstance(message.content, list)
            or not all(isinstance(block, TextContent) for block in message.content)
        ):
            pruned.append(message)
            continue
        text = message.text()
        if (
            len(text) < max(0, policy.min_chars)
            or policy.keep_chars >= len(text)
            or text.startswith(PRUNE_PREFIX)
        ):
            pruned.append(message)
            continue
        spill = _spill(text, tool_name=message.tool_name, directory=policy.spill_dir)
        stub = _stub(
            text, tool_name=message.tool_name, spill=spill, keep_chars=policy.keep_chars
        )
        pruned.append(
            message.model_copy(update={"content": [TextContent(text=stub)]})
        )
        changed = True
    return pruned if changed else messages


__all__ = [
    "PRUNE_PREFIX",
    "PrunePolicy",
    "policy_from_settings",
    "prune_tool_results",
    "resolve_spill_dir",
]
