"""Configuration for the tm_im integration.

Credentials and options live in ``<config>/im.toml`` (created with mode 0600 by
``tm im login``). Environment variables override the file so a bot can be run
without persisting a key.
"""

from __future__ import annotations

import contextlib
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import tomli_w

from tm.config import config_dir, read_toml

ALLOW_TOOLS = ("none", "read_only", "full")
RESPOND_IN_GROUPS = ("mention", "always", "never")
ACCEPT_FRIENDS = ("none", "allowlist", "all")


def im_path() -> Path:
    return config_dir() / "im.toml"


def _normalize_choice(value: str, allowed: tuple[str, ...], default: str) -> str:
    normalized = (value or "").strip().lower().replace("-", "_")
    return normalized if normalized in allowed else default


@dataclass
class ImSettings:
    base_url: str = "http://localhost:8080/v1"
    api_key: str = ""
    tcp_addr: str | None = None
    device_id: str = "tm"
    allow_tools: str = "full"
    respond_in_groups: str = "always"
    auto_accept_friends: str = "none"
    owner_handles: list[str] = field(default_factory=list)
    reply_max_chars: int = 4000
    sync_limit: int = 200
    client_version: str = "tm"
    provider: str | None = None
    model: str | None = None
    system_prompt: str | None = None

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).hostname or "localhost"

    def realtime_address(self) -> tuple[str, int]:
        """Host and port for the realtime TCP connection."""
        if self.tcp_addr:
            host, _, port = self.tcp_addr.rpartition(":")
            return (host or self.host, int(port))
        return (self.host, 8090)


def _from_mapping(settings: ImSettings, data: dict) -> ImSettings:
    fields = {
        key: value
        for key, value in data.items()
        if key in ImSettings.__dataclass_fields__ and not key.startswith("_")
    }
    for key, value in fields.items():
        if key == "owner_handles" and isinstance(value, list):
            setattr(settings, key, [str(item) for item in value])
        elif isinstance(value, (str, int)) or value is None:
            setattr(settings, key, value)
    return settings


def load_im_settings(path: Path | None = None) -> ImSettings:
    settings = ImSettings()
    target = path or im_path()
    if target.exists():
        with contextlib.suppress(OSError, tomllib.TOMLDecodeError):
            _from_mapping(settings, read_toml(target))

    if env := os.environ.get("TM_IM_BASE_URL"):
        settings.base_url = env
    if env := os.environ.get("TM_IM_API_KEY"):
        settings.api_key = env
    if env := os.environ.get("TM_IM_TCP_ADDR"):
        settings.tcp_addr = env
    if env := os.environ.get("TM_IM_ALLOW_TOOLS"):
        settings.allow_tools = _normalize_choice(env, ALLOW_TOOLS, settings.allow_tools)
    if env := os.environ.get("TM_IM_RESPOND_IN_GROUPS"):
        settings.respond_in_groups = _normalize_choice(
            env, RESPOND_IN_GROUPS, settings.respond_in_groups
        )

    settings.allow_tools = _normalize_choice(settings.allow_tools, ALLOW_TOOLS, "full")
    settings.respond_in_groups = _normalize_choice(
        settings.respond_in_groups, RESPOND_IN_GROUPS, "always"
    )
    settings.auto_accept_friends = _normalize_choice(
        settings.auto_accept_friends, ACCEPT_FRIENDS, "none"
    )
    return settings


def save_im_settings(settings: ImSettings, path: Path | None = None) -> Path:
    target = path or im_path()
    data = {
        "base_url": settings.base_url,
        "api_key": settings.api_key,
        "device_id": settings.device_id,
        "allow_tools": settings.allow_tools,
        "respond_in_groups": settings.respond_in_groups,
        "auto_accept_friends": settings.auto_accept_friends,
        "owner_handles": list(settings.owner_handles),
        "reply_max_chars": settings.reply_max_chars,
    }
    if settings.tcp_addr:
        data["tcp_addr"] = settings.tcp_addr
    if settings.provider:
        data["provider"] = settings.provider
    if settings.model:
        data["model"] = settings.model

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as handle:
        tomli_w.dump(data, handle)
    with contextlib.suppress(OSError):
        os.chmod(target, 0o600)
    return target


__all__ = [
    "ACCEPT_FRIENDS",
    "ALLOW_TOOLS",
    "ImSettings",
    "RESPOND_IN_GROUPS",
    "im_path",
    "load_im_settings",
    "save_im_settings",
]
