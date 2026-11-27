"""Python client for the embedding protocol (``tm serve --stdio``)."""

from __future__ import annotations

from tm.sdk.client import (
    DEFAULT_COMMAND,
    ChangedFile,
    PermissionHandler,
    TmClient,
    TmError,
    TmSession,
    Turn,
    TurnResult,
)

__all__ = [
    "DEFAULT_COMMAND",
    "ChangedFile",
    "PermissionHandler",
    "TmClient",
    "TmError",
    "TmSession",
    "Turn",
    "TurnResult",
]
