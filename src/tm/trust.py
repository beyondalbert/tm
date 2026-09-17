"""Project trust: gate project-local resources before loading them.

A repository can contain resources that execute code (``.aiagent/extensions``)
or weaken the permission policy (``.aiagent/policy.toml``). Loading those
without asking would let an untrusted checkout change TM's behavior before the
user approves it. TM therefore asks before loading project-local resources and
remembers the decision per directory in ``<config>/trust.json``.

Project **context** files (``AGENTS.md`` / ``CLAUDE.md``) are not gated: they are
plain text appended to the prompt, matching pi.
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_RESOURCE_DIRS: tuple[str, ...] = (
    ".aiagent/extensions",
    ".aiagent/skills",
    ".aiagent/prompts",
    ".aiagent/themes",
    ".agents/skills",
)

PROJECT_RESOURCE_FILES: tuple[str, ...] = (
    ".aiagent/settings.toml",
    ".aiagent/SYSTEM.md",
    ".aiagent/APPEND_SYSTEM.md",
    ".aiagent/policy.toml",
)


def _has_entries(path: Path) -> bool:
    try:
        return any(path.iterdir())
    except OSError:
        return False


def project_resources(cwd: Path) -> list[Path]:
    """Project-local resources that require trust to load.

    A bare ``.aiagent`` directory does not count; only resources that change
    behavior.
    """
    found: list[Path] = []
    for rel in PROJECT_RESOURCE_DIRS:
        candidate = cwd / rel
        if candidate.is_dir() and _has_entries(candidate):
            found.append(candidate)
    for rel in PROJECT_RESOURCE_FILES:
        candidate = cwd / rel
        if candidate.is_file():
            found.append(candidate)
    return found


class TrustManager:
    """Persists per-directory trust decisions in ``trust.json``."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @staticmethod
    def canon(cwd: Path) -> str:
        return str(cwd.resolve())

    def _load(self) -> dict[str, bool]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("rb") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): bool(value) for key, value in data.items()}

    def decision(self, cwd: Path) -> bool | None:
        """The nearest saved decision for ``cwd`` or one of its parents."""
        decisions = self._load()
        for directory in (cwd.resolve(), *cwd.resolve().parents):
            found = decisions.get(str(directory))
            if found is not None:
                return found
        return None

    def save(self, cwd: Path, trusted: bool) -> None:
        decisions = self._load()
        decisions[self.canon(cwd)] = trusted
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(decisions, handle, indent=2, sort_keys=True)


__all__ = ["PROJECT_RESOURCE_DIRS", "PROJECT_RESOURCE_FILES", "TrustManager", "project_resources"]
