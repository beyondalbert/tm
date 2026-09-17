"""Editor autocomplete: ``/`` command and ``@file`` completion.

Pure logic, independent of the UI, so it is easy to test:

* :func:`detect` finds the token under the cursor (a command or a file reference).
* :func:`command_completions` / :class:`FileIndex` produce ranked candidates.
* :func:`apply_completion` splices the chosen value back into the line.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".idea",
        ".vscode",
        "dist",
        "build",
    }
)

MAX_FILES = 20_000


@dataclass
class Completion:
    value: str
    label: str
    description: str = ""


@dataclass
class Span:
    kind: str  # "command" | "file"
    token: str
    start: int
    end: int


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def detect(text: str, cursor: int) -> Span | None:
    """The completion token at ``cursor`` (0-based), or None."""
    cursor = max(0, min(cursor, len(text)))
    if text.startswith("/") and " " not in text[:cursor]:
        return Span("command", text[:cursor], 0, cursor)
    start = cursor
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    if start < cursor and text[start] == "@":
        return Span("file", text[start:cursor], start, cursor)
    return None


def apply_completion(text: str, span: Span, value: str) -> str:
    """Replace the token at ``span`` with ``value`` and add a trailing space."""
    return f"{text[: span.start]}{value}{text[span.end:]} "


def command_completions(
    token: str, specs: list[tuple[str, str]], limit: int = 20
) -> list[Completion]:
    prefix = token[1:] if token.startswith("/") else token
    scored: list[tuple[float, Completion]] = []
    for name, description in specs:
        if not prefix:
            score = 1.0
        elif name.startswith(prefix):
            score = 2.0
        else:
            score = _ratio(prefix, name)
        if score >= 0.5:
            scored.append((score, Completion(f"/{name}", f"/{name}", description)))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [completion for _, completion in scored[:limit]]


class FileIndex:
    """Lazily built, fuzzy-matched index of files under a root."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._files: list[str] | None = None

    def build(self) -> list[str]:
        files: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                try:
                    relative = Path(dirpath, name).relative_to(self.root).as_posix()
                except ValueError:
                    continue
                files.append(relative)
                if len(files) >= MAX_FILES:
                    return files
        return files

    def _ensure(self) -> list[str]:
        if self._files is None:
            self._files = self.build()
        return self._files

    def match(self, prefix: str, limit: int = 20) -> list[Completion]:
        prefix = prefix.lower()
        files = self._ensure()
        if not prefix:
            return [Completion(p, p) for p in sorted(files)[:limit]]
        scored: list[tuple[float, str]] = []
        for path in files:
            lowered = path.lower()
            index = lowered.find(prefix)
            if index != -1:
                score = 1000.0 - index * 5 - len(path) * 0.01
            else:
                ratio = _ratio(prefix, lowered)
                if ratio < 0.6:
                    continue
                score = ratio * 100
            scored.append((score, path))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [Completion(path, path) for _, path in scored[:limit]]


__all__ = [
    "Completion",
    "FileIndex",
    "Span",
    "apply_completion",
    "command_completions",
    "detect",
]
