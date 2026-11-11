"""Strip terminal escape sequences and control characters from display text.

Textual only strips Bell/Backspace/VT/FF/CR, not ESC, so a model reply or tool
output containing ANSI sequences is written straight to the terminal and
corrupts the screen. That applies to every renderer, not just the TUI, so the
sanitizer lives here and is shared by both.

Three cases matter beyond plain SGR:

* 8-bit C1 controls (``\\x9b`` CSI, ``\\x9d`` OSC, ...), which providers and
  editor-shaped tool output do emit.
* A sequence cut off by a stream boundary: the introducer must go together with
  its parameters, otherwise removing only the ESC leaves ``[38;5;32`` visible.
* A parameter list whose ESC was already lost upstream (``[555;5;32M``). Only a
  distinctive ANSI shape is matched, so ordinary bracket text survives.
"""

from __future__ import annotations

import re

#: Escape sequences, including 8-bit C1 forms and unfinished sequences.
_ANSI_RE = re.compile(
    # OSC: ESC ] ... BEL or ST
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"
    # OSC with the 8-bit C1 introducer
    r"|\x9d[^\x07\x9c]*(?:\x07|\x9c)?"
    # DCS / SOS / PM / APC with a 7-bit introducer
    r"|\x1b[PX^_][^\x1b]*(?:\x1b\\)?"
    # DCS / SOS / PM / APC with an 8-bit C1 introducer
    r"|[\x90\x98\x9e\x9f][^\x9c]*(?:\x9c)?"
    # CSI (SGR, cursor movement, ...). The final byte is optional so a sequence
    # cut off by a stream boundary is removed together with its parameters.
    r"|\x1b\[[0-?]*[ -/]*[@-~]?"
    r"|\x9b[0-?]*[ -/]*[@-~]?"
    # Two-character Fe escapes, and a bare ESC left over from anything else.
    r"|\x1b[@-Z\\-_]?",
    re.DOTALL,
)

#: A CSI parameter list left behind after its ESC was lost. Requires at least
#: two separators, so markdown links and prose brackets are not touched.
_ORPHAN_CSI_RE = re.compile(r"\[[0-9]{1,4}(?:;[0-9]{1,3}){2,}[A-Za-z]")

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def sanitize(text: str) -> str:
    """Remove terminal escape sequences and control characters from display text."""
    if not text:
        return text
    return _CONTROL_RE.sub("", _ORPHAN_CSI_RE.sub("", _ANSI_RE.sub("", text)))


__all__ = ["sanitize"]
