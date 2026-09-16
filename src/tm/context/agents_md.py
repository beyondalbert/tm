"""Loading of AGENTS.md / CLAUDE.md context files."""

from __future__ import annotations

from pathlib import Path

CONTEXT_FILENAMES = ("AGENTS.md", "CLAUDE.md")
OVERRIDE_FILENAME = "AGENTS.override.md"


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def load_context_files(
    cwd: Path, global_dir: Path | None = None
) -> list[tuple[Path, str]]:
    """Collect context files from the global dir and from root down to cwd."""
    files: list[tuple[Path, str]] = []
    if global_dir is not None:
        for name in CONTEXT_FILENAMES:
            path = global_dir / name
            text = _read(path)
            if text is not None:
                files.append((path, text))

    directories = [cwd, *cwd.parents]
    for directory in reversed(directories):
        override = directory / OVERRIDE_FILENAME
        text = _read(override)
        if text is not None:
            files.append((override, text))
            continue
        for name in CONTEXT_FILENAMES:
            candidate = directory / name
            text = _read(candidate)
            if text is not None:
                files.append((candidate, text))
                break
    return files


def build_context_section(files: list[tuple[Path, str]]) -> str:
    if not files:
        return ""
    parts = [f"Context from {path}:\n{text}" for path, text in files]
    return "\n\n".join(parts)


__all__ = [
    "CONTEXT_FILENAMES",
    "OVERRIDE_FILENAME",
    "build_context_section",
    "load_context_files",
]
