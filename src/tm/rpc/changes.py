"""Turn-scoped view of the change journal.

``turn/end`` reports which files a turn touched, with a unified diff when the
previous content is known. The journal already stores the content *before* each
change (``undo.content_b64``), so a per-turn diff only needs the current file.
"""

from __future__ import annotations

import base64
import difflib
from pathlib import Path
from typing import Any

from tm.safety import Change, Journal

MAX_DIFF_LINES = 2000
MAX_FILE_BYTES = 512 * 1024


def _decode(encoded: Any) -> bytes | None:
    if not isinstance(encoded, str) or not encoded:
        return None
    try:
        return base64.b64decode(encoded)
    except (ValueError, TypeError):
        return None


def _as_text(data: bytes | None) -> str | None:
    if data is None:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _diff(old: str, new: str, name: str) -> tuple[str, int, int, bool]:
    lines = list(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
            lineterm="",
        )
    )
    added = sum(
        1 for line in lines if line.startswith("+") and not line.startswith("+++")
    )
    removed = sum(
        1 for line in lines if line.startswith("-") and not line.startswith("---")
    )
    truncated = len(lines) > MAX_DIFF_LINES
    if truncated:
        lines = lines[:MAX_DIFF_LINES] + ["... (diff truncated)"]
    return "\n".join(lines), added, removed, truncated


def _entry(first: Change, last: Change) -> dict[str, Any]:
    item: dict[str, Any] = {
        "path": last.target,
        "kind": last.kind,
        "tool": last.tool,
        "summary": last.summary,
        "status": last.status,
    }
    if last.kind != "file":
        return item
    path = Path(str(first.undo.get("path") or last.target))
    existed = bool(first.undo.get("existed"))
    old = "" if not existed else _as_text(_decode(first.undo.get("content_b64")))
    item["exists"] = path.is_file()
    new = _read_text(path) if item["exists"] else ""
    if old is None or new is None:
        item.update({"added": None, "removed": None, "diff": None, "truncated": False})
        return item
    diff, added, removed, truncated = _diff(old, new, path.name)
    item.update(
        {"added": added, "removed": removed, "diff": diff, "truncated": truncated}
    )
    return item


def collect_changes(
    journal: Journal | None,
    *,
    session_id: str | None,
    since_ts: float,
    any_session: bool = False,
) -> list[dict[str, Any]]:
    """File changes this session made at or after ``since_ts``, one entry per path."""
    if journal is None:
        return []
    first: dict[str, Change] = {}
    last: dict[str, Change] = {}
    order: list[str] = []
    for change in journal.entries(session=None if any_session else session_id):
        if change.ts < since_ts:
            continue
        if change.target not in first:
            first[change.target] = change
            order.append(change.target)
        last[change.target] = change
    return [_entry(first[target], last[target]) for target in order]


__all__ = ["collect_changes"]
