"""Detect shell commands that are destructive or irreversible.

Matching a pattern forces an explicit, non-remembered approval rather than a
hard deny, so the user can still allow a legitimate command.
"""

from __future__ import annotations

DANGEROUS_PATTERNS: tuple[str, ...] = (
    "rm -rf /",
    "rm -fr /",
    "rm -rf ~",
    "rm -rf *",
    "mkfs",
    "dd if=",
    "dd of=",
    "of=/dev/",
    "> /dev/sd",
    "> /dev/nvme",
    ":(){:|:&};:",
    "shutdown",
    "reboot",
    "poweroff",
    "halt",
    "format ",
    "diskpart",
    "reg delete hklm",
    "bcdedit",
    "drop database",
    "truncate table",
    "chmod -r 777 /",
    "chown -r",
    "userdel",
    "vssadmin delete",
)


def is_dangerous(command: str) -> bool:
    normalized = " ".join(command.strip().lower().split())
    if not normalized:
        return False
    return any(pattern in normalized for pattern in DANGEROUS_PATTERNS)


__all__ = ["DANGEROUS_PATTERNS", "is_dangerous"]
