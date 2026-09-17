"""Content redaction for telemetry.

One choke point drops anything that could carry user content, secrets, or tool
payloads. Adapters that follow the contract always receive redacted spans.

Policy: an allowlist of safe scalar keys is *not* used. Instead, a denylist of
keys known to hold content is replaced with a sentinel, and any value that is not
a scalar (or a small list of scalars) is dropped entirely. This is conservative:
unknown non-scalar structures never leak.
"""

from __future__ import annotations

from typing import Any

REDACTED = "<redacted>"

# Keys whose values are user content or secrets and must never be reported.
# Matching is case-insensitive and substring-based so ``tool_args`` and
# ``arguments`` both match.
REDACTED_KEYS: frozenset[str] = frozenset(
    {
        "prompt",
        "content",
        "message",
        "messages",
        "text",
        "arguments",
        "args",
        "output",
        "result",
        "input",
        "command",
        "path",
        "data",
        "body",
        "api_key",
        "apikey",
        "token",
        "secret",
        "password",
        "authorization",
        "file",
        "diff",
        "patch",
    }
)

_MAX_LIST = 32


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(blocked in lowered for blocked in REDACTED_KEYS)


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def redact(attributes: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``attributes`` safe for telemetry.

    Secret/content keys become the sentinel. Non-scalar values are dropped unless
    they are lists of scalars, which are truncated. Nested dicts are removed
    rather than recursed, so no unknown structure can leak by accident.
    """
    clean: dict[str, Any] = {}
    for key, value in attributes.items():
        if _is_secret_key(key):
            clean[key] = REDACTED
            continue
        if _is_scalar(value):
            clean[key] = value
            continue
        if isinstance(value, (list, tuple)) and all(_is_scalar(v) for v in value):
            clean[key] = list(value[:_MAX_LIST])
            continue
        # Unknown / structured value: drop it entirely.
    return clean


__all__ = ["REDACTED", "REDACTED_KEYS", "redact"]
