"""Host detection."""

from __future__ import annotations

import os

from tm.host.base import Host
from tm.host.linux import LinuxHost
from tm.host.windows import WindowsHost


def detect_host() -> Host:
    """Return the host for the current platform.

    macOS falls back to :class:`LinuxHost` (best-effort); it is not a supported
    target yet.
    """
    if os.name == "nt":
        return WindowsHost()
    return LinuxHost()


__all__ = ["detect_host"]
