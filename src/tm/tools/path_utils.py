from __future__ import annotations

from pathlib import Path


def resolve_path(cwd: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path


__all__ = ["resolve_path"]
