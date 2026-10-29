"""Terminal capability checks for the TUI.

The TUI (Textual) needs a real terminal, a sane ``TERM``, and enough rows and
columns. ``ensure_utf8`` also matters over SSH: a server locale such as
``zh_CN.GBK`` makes Python encode non-ASCII output as GBK, which raises in
Textual's writer thread on glyphs such as box drawing or the spinner, leaving a
frozen or half-drawn screen.
"""

from __future__ import annotations

import importlib.metadata
import os
import sys
from dataclasses import dataclass

MIN_COLUMNS = 20
MIN_ROWS = 8
_SSH_ENV = ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")
_DUMB_TERMS = ("", "dumb", "unknown")


@dataclass(frozen=True)
class TerminalInfo:
    term: str
    colorterm: str
    no_color: bool
    is_tty: bool
    encoding: str
    columns: int
    rows: int
    ssh: bool
    multiplexer: str | None
    notes: tuple[str, ...]


def ensure_utf8() -> None:
    """Make stdout/stderr encode UTF-8, so the TUI never dies on wide glyphs."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (LookupError, OSError, ValueError):
            continue


def _size() -> tuple[int, int]:
    try:
        size = os.get_terminal_size(sys.stdout.fileno())
    except (OSError, ValueError):
        return (0, 0)
    return (size.columns, size.lines)


def _version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def terminal_info() -> TerminalInfo:
    term = os.environ.get("TERM", "")
    colorterm = os.environ.get("COLORTERM", "")
    try:
        is_tty = bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        is_tty = False
    encoding = getattr(sys.stdout, "encoding", "") or ""
    columns, rows = _size()
    ssh = any(os.environ.get(name) for name in _SSH_ENV)
    multiplexer = None
    if os.environ.get("TMUX"):
        multiplexer = "tmux"
    elif os.environ.get("STY"):
        multiplexer = "screen"

    notes: list[str] = []
    if term in _DUMB_TERMS:
        notes.append(f"TERM is {term or 'unset'}; a TUI needs a real terminal")
    if not is_tty:
        notes.append("stdout is not a TTY")
    if encoding and encoding.lower().replace("-", "") not in ("utf8", "utf8mb4"):
        notes.append(f"stdout encoding is {encoding}; the TUI switches to UTF-8")
    if columns < MIN_COLUMNS or rows < MIN_ROWS:
        notes.append(f"terminal size is {columns}x{rows}; the TUI needs at least {MIN_COLUMNS}x{MIN_ROWS}")
    if ssh and multiplexer is None:
        notes.append("SSH session without tmux/screen; if rendering is broken try --tui-inline or tmux")
    if not colorterm:
        notes.append(
            "COLORTERM is unset; color support comes from TERM (xterm is stepped up to 256)"
        )
    return TerminalInfo(
        term=term,
        colorterm=colorterm,
        no_color=bool(os.environ.get("NO_COLOR")),
        is_tty=is_tty,
        encoding=encoding,
        columns=columns,
        rows=rows,
        ssh=ssh,
        multiplexer=multiplexer,
        notes=tuple(notes),
    )


_COLOR_SYSTEM_ALIASES = {
    "16": "standard",
    "standard": "standard",
    "ansi": "standard",
    "256": "256",
    "256color": "256",
    "truecolor": "truecolor",
    "24bit": "truecolor",
    "rgb": "truecolor",
}


def preferred_color_system() -> str | None:
    """The Textual color system to force, or None to let it auto-detect.

    ``TERM=xterm`` (common when an SSH client does not advertise 256 colors)
    makes Rich fall back to 16 colors, which collapses backgrounds and text on
    terminals such as Xshell. Xshell and most xterm emulators do support 256
    colors, so step up unless the user overrides it.
    """
    if os.environ.get("TEXTUAL_COLOR_SYSTEM"):
        return None
    requested = os.environ.get("TM_TUI_COLOR_SYSTEM", "").strip().lower()
    if not requested or requested == "auto":
        if os.environ.get("COLORTERM"):
            return None
        if os.environ.get("TERM", "") not in ("xterm", "xterm-color"):
            return None
        requested = "256"
    return _COLOR_SYSTEM_ALIASES.get(requested)


def prepare_tui_env() -> str | None:
    """Set ``TEXTUAL_COLOR_SYSTEM`` before Textual is imported."""
    value = preferred_color_system()
    if value is not None:
        os.environ["TEXTUAL_COLOR_SYSTEM"] = value
    return value


def tui_problem(info: TerminalInfo | None = None) -> str | None:
    """Why the TUI cannot render here, or None when it should be usable."""
    info = info or terminal_info()
    if not info.is_tty:
        return "stdout is not a TTY"
    if info.term in _DUMB_TERMS:
        return f"TERM={info.term or '(unset)'}"
    if info.columns < MIN_COLUMNS or info.rows < MIN_ROWS:
        return f"terminal is too small ({info.columns}x{info.rows})"
    return None


def format_terminal_info(info: TerminalInfo | None = None) -> str:
    """A diagnostic block for ``tm --terminal-info``."""
    info = info or terminal_info()
    python = sys.version.split()[0]
    lines = [
        f"python: {python}",
        f"term: {info.term or '(unset)'}",
        f"colorterm: {info.colorterm or '(unset)'}",
        f"no_color: {'yes' if info.no_color else 'no'}",
        f"tty: {'yes' if info.is_tty else 'no'}",
        f"encoding: {info.encoding or '(unknown)'}",
        f"size: {info.columns}x{info.rows}",
        f"session: {'ssh' if info.ssh else 'local'}"
        + (f" + {info.multiplexer}" if info.multiplexer else ""),
        f"color_system: {preferred_color_system() or 'auto'}",
        f"textual: {_version('textual')}",
        f"rich: {_version('rich')}",
    ]
    problem = tui_problem(info)
    lines.append(f"tui: {'usable' if problem is None else 'not usable: ' + problem}")
    if info.notes:
        lines.append("notes:")
        lines.extend(f"  - {note}" for note in info.notes)
    lines.extend(
        [
            "hints:",
            "  - broken colors or overlapping text over SSH: try `tm --tui-inline`,",
            "    run inside tmux, or use `tm --no-tui` (plain console)",
            "  - Xshell/SecureCRT: use UTF-8 encoding and an xterm-256color terminal type",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "MIN_COLUMNS",
    "MIN_ROWS",
    "TerminalInfo",
    "ensure_utf8",
    "format_terminal_info",
    "preferred_color_system",
    "prepare_tui_env",
    "terminal_info",
    "tui_problem",
]
