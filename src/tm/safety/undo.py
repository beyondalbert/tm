"""Replay a change's undo data to restore the previous state."""

from __future__ import annotations

import base64
from pathlib import Path

from tm.host.base import Host
from tm.safety.journal import Change


def apply_undo(change: Change, host: Host) -> str:
    if change.kind == "file":
        return _undo_file(change)
    if change.kind == "package":
        return _undo_package(change, host)
    if change.kind == "service":
        return _undo_service(change, host)
    return f"cannot undo a '{change.kind}' change"


def _undo_file(change: Change) -> str:
    path = Path(str(change.undo.get("path", "")))
    existed = bool(change.undo.get("existed"))
    encoded = change.undo.get("content_b64")
    if existed and isinstance(encoded, str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(encoded))
        return f"restored {path}"
    if path.exists():
        path.unlink()
        return f"deleted {path}"
    return f"{path} already absent"


def _undo_package(change: Change, host: Host) -> str:
    name = str(change.undo.get("name", ""))
    action = str(change.undo.get("action", ""))
    if action == "install":
        ok, output = host.package_action("uninstall", [name])
        verb = "uninstalled"
    else:
        ok, output = host.package_action("install", [name])
        verb = "reinstalled"
    return f"{verb} {name}" if ok else f"failed to undo package {name}: {output}"


def _undo_service(change: Change, host: Host) -> str:
    name = str(change.undo.get("name", ""))
    was_active = bool(change.undo.get("was_active"))
    action = "start" if was_active else "stop"
    ok, output = host.service_action(name, action)
    return f"{action} {name}" if ok else f"failed to undo service {name}: {output}"


__all__ = ["apply_undo"]
