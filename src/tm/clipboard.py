"""Copy text to the operating system clipboard.

Textual's built-in ``copy_to_clipboard`` uses OSC 52, which many terminals
(notably on Windows) ignore. This module writes to the real system clipboard
instead, falling back to nothing when no mechanism is available.
"""

from __future__ import annotations

import shutil
import subprocess
import sys


def copy_to_clipboard(text: str) -> bool:
    """Copy ``text`` to the system clipboard. Returns True on success."""
    if not text:
        return False
    if sys.platform == "win32":
        return _copy_windows(text)
    if sys.platform == "darwin":
        return _pipe(["pbcopy"], text)
    for command in (
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ):
        if _pipe(command, text):
            return True
    return False


def _pipe(argv: list[str], text: str) -> bool:
    if shutil.which(argv[0]) is None:
        return False
    try:
        completed = subprocess.run(
            argv, input=text.encode("utf-8"), timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _copy_windows(text: str) -> bool:
    import ctypes

    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return False
    user32 = windll.user32
    kernel32 = windll.kernel32
    cf_unicodetext = 13
    gmem_moveable = 0x0002

    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

    if not user32.OpenClipboard(None):
        return False
    try:
        if not user32.EmptyClipboard():
            return False
        data = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(gmem_moveable, len(data))
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(cf_unicodetext, handle):
            kernel32.GlobalFree(handle)
            return False
        return True
    finally:
        user32.CloseClipboard()


__all__ = ["copy_to_clipboard"]
