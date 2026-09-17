"""Safety and reversibility: dangerous-command detection and change journal."""

from __future__ import annotations

from tm.safety.danger import DANGEROUS_PATTERNS, is_dangerous
from tm.safety.journal import Change, Journal, new_change, record_file_change
from tm.safety.undo import apply_undo

__all__ = [
    "DANGEROUS_PATTERNS",
    "Change",
    "Journal",
    "apply_undo",
    "is_dangerous",
    "new_change",
    "record_file_change",
]
