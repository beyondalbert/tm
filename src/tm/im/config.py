"""Configuration for the tm_im integration.

Credentials and options live in ``<config>/im.toml`` (created with mode 0600 by
``tm im login``). Environment variables override the file so a bot can be run
without persisting a key.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from urllib.parse import urlsplit

import tomli_w

from tm.config import config_dir, read_toml

ALLOW_TOOLS = ("none", "read_only", "full")
RESPOND_IN_GROUPS = ("mention", "always", "never")
ACCEPT_FRIENDS = ("none", "allowlist", "all")


def im_path() -> Path:
    return config_dir() / "im.toml"


def configs_dir() -> Path:
    """Directory for named config files created by ``tm im config new``."""
    return config_dir() / "configs"


def named_config_path(name: str) -> Path:
    filename = name if name.endswith(".toml") else f"{name}.toml"
    return configs_dir() / filename


def resolve_config(value: str | None) -> str | None:
    """Turn a ``--config`` value into a concrete path.

    A bare name (no path separator) is a named config under ``configs/``; a
    value with separators is used as an explicit path. ``None`` means the
    default ``<config>/im.toml``.
    """
    if value is None or value == "":
        return None
    text = str(value)
    separators = (os.sep, os.altsep) if os.altsep else (os.sep,)
    if any(sep and sep in text for sep in separators):
        return str(Path(text).expanduser())
    return str(named_config_path(text))


def instance_dir(config: str | None) -> Path:
    """The on-disk directory for one ``tm im`` instance.

    The default config uses ``<config>/im``; any other ``--config`` file gets
    its own subdirectory so instances do not share state, sessions, the
    pidfile, or the log.
    """
    base = config_dir() / "im"
    if config is None:
        return base
    path = Path(config).expanduser()
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    digest = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:8]
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", path.stem) or "im"
    return base / "instances" / f"{name}-{digest}"


def _normalize_choice(value: str, allowed: tuple[str, ...], default: str) -> str:
    normalized = (value or "").strip().lower().replace("-", "_")
    return normalized if normalized in allowed else default


@dataclass
class ImSettings:
    base_url: str = "http://localhost:8080/v1"
    api_key: str = ""
    tcp_addr: str | None = None
    ws_url: str | None = None
    device_id: str = "tm"
    allow_tools: str = "full"
    respond_in_groups: str = "always"
    auto_accept_friends: str = "none"
    owner_handles: list[str] = field(default_factory=list)
    reply_max_chars: int = 4000
    sync_limit: int = 200
    ack_reply: str = "收到，正在处理：{preview}"
    ack_preview_chars: int = 60
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

    def websocket_url(self) -> str | None:
        """The realtime WebSocket URL, normalized from ``ws_url``.

        Accepts ``host/ws``, ``ws://host/ws``, ``wss://host:9000/ws``, or even a
        full REST URL like ``http://host/ws`` (``http`` -> ``ws``, ``https`` ->
        ``wss``). A value without a scheme inherits ``ws``/``wss`` from
        ``base_url``, so ``https://host/v1`` yields ``wss://host/ws``.
        """
        raw = (self.ws_url or "").strip()
        if not raw:
            return None
        # Collapse a doubled scheme such as "ws://http://host/ws".
        raw = re.sub(
            r"^[A-Za-z][A-Za-z0-9+.\-]*://(?=[A-Za-z][A-Za-z0-9+.\-]*://)", "", raw
        )
        default_scheme = "wss" if self.base_url.startswith("https") else "ws"
        if "://" in raw:
            parsed = urlsplit(raw)
            scheme = parsed.scheme.lower()
            if scheme in ("http", "ws"):
                scheme = "ws"
            elif scheme in ("https", "wss"):
                scheme = "wss"
            else:
                scheme = default_scheme
            host, port, path = parsed.hostname, parsed.port, parsed.path
            query = parsed.query
        else:
            scheme = default_scheme
            parsed = urlsplit(f"//{raw}")
            host, port, path = parsed.hostname, parsed.port, parsed.path
            query = parsed.query
        host = host or self.host
        netloc = f"{host}:{port}" if port else host
        path = path or "/"
        if query:
            path = f"{path}?{query}"
        return f"{scheme}://{netloc}{path}"


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


def load_im_settings(path: Path | None = None, *, apply_env: bool = True) -> ImSettings:
    settings = ImSettings()
    target = path or im_path()
    if target.exists():
        with contextlib.suppress(OSError, tomllib.TOMLDecodeError):
            _from_mapping(settings, read_toml(target))

    if apply_env:
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
        if env := os.environ.get("TM_IM_ACK_REPLY"):
            settings.ack_reply = env

    settings.allow_tools = _normalize_choice(settings.allow_tools, ALLOW_TOOLS, "full")
    settings.respond_in_groups = _normalize_choice(
        settings.respond_in_groups, RESPOND_IN_GROUPS, "always"
    )
    settings.auto_accept_friends = _normalize_choice(
        settings.auto_accept_friends, ACCEPT_FRIENDS, "none"
    )
    return settings


_INT_FIELDS = ("reply_max_chars", "sync_limit", "ack_preview_chars")
_CHOICE_FIELDS = {
    "allow_tools": ALLOW_TOOLS,
    "respond_in_groups": RESPOND_IN_GROUPS,
    "auto_accept_friends": ACCEPT_FRIENDS,
}


def apply_setting(settings: ImSettings, assignment: str) -> str:
    """Apply one ``key=value`` assignment to ``settings``; return the key.

    Raises ``ValueError`` for an unknown key or an invalid value.
    """
    key, separator, value = assignment.partition("=")
    key = key.strip().lower().replace("-", "_")
    value = value.strip()
    if not separator or not key:
        raise ValueError(f"expected key=value, got: {assignment}")
    if key not in ImSettings.__dataclass_fields__ or key.startswith("_"):
        raise ValueError(f"unknown field: {key}")
    if key in _INT_FIELDS:
        try:
            setattr(settings, key, int(value))
        except ValueError as error:
            raise ValueError(f"{key} must be an integer") from error
    elif key == "owner_handles":
        setattr(settings, key, [item.strip() for item in value.split(",") if item.strip()])
    else:
        setattr(settings, key, value)
    if key in _CHOICE_FIELDS:
        allowed = _CHOICE_FIELDS[key]
        normalized = _normalize_choice(value, allowed, "")
        if not normalized:
            raise ValueError(f"{key} must be one of {', '.join(allowed)}")
        setattr(settings, key, normalized)
    return key


def save_im_settings(settings: ImSettings, path: Path | None = None) -> Path:
    target = path or im_path()
    data: dict = {}
    for item in fields(ImSettings):
        value = getattr(settings, item.name)
        if value is None:
            continue
        data[item.name] = value

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
    "apply_setting",
    "configs_dir",
    "im_path",
    "instance_dir",
    "load_im_settings",
    "named_config_path",
    "resolve_config",
    "save_im_settings",
]
