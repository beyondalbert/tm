"""Configuration loading for The Machine."""

from __future__ import annotations

import contextlib
import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

import tomli_w


def config_dir() -> Path:
    override = os.environ.get("TM_CONFIG_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "the-machine"


def settings_path() -> Path:
    return config_dir() / "settings.toml"


def credentials_path() -> Path:
    return config_dir() / "credentials.toml"


@dataclass
class Settings:
    provider: str = "deepseek"
    model: str = "deepseek-v4-pro"
    temperature: float | None = None
    max_tokens: int | None = None
    system_prompt: str | None = None
    auto_compact: bool = True
    compact_threshold: float = 0.8
    compact_keep_recent: int = 6
    telemetry: bool = False
    durable: bool = False


def load_settings() -> Settings:
    path = settings_path()
    settings = Settings()
    if not path.exists():
        return settings
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    known = {f.name for f in fields(Settings)}
    for key, value in data.items():
        if key in known:
            setattr(settings, key, value)
    return settings


def sanitize_api_key(raw: str) -> str:
    """Normalize a pasted API key.

    Handles the ways a key can arrive malformed: stray whitespace/newlines and
    accidental repetition (a terminal that pastes the same key several times
    yields e.g. 105 chars instead of 35).
    """
    key = "".join(raw.split()).strip().strip('"').strip("'")
    length = len(key)
    if length >= 8:
        for period in range(1, length // 2 + 1):
            if length % period == 0 and key == key[:period] * (length // period):
                key = key[:period]
                break
    return key


def load_credentials() -> dict[str, str]:
    """Read stored API keys: ``[providers] provider = "sk-..."``."""
    path = credentials_path()
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    providers = data.get("providers", {})
    if not isinstance(providers, dict):
        return {}
    return {
        str(key): sanitize_api_key(value)
        for key, value in providers.items()
        if isinstance(value, str)
    }


def save_credential(provider: str, key: str) -> Path:
    path = credentials_path()
    data: dict = {}
    if path.exists():
        try:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            data = {}
    providers = data.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    providers[provider] = sanitize_api_key(key)
    data["providers"] = providers

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        tomli_w.dump(data, handle)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    return path


__all__ = [
    "Settings",
    "config_dir",
    "credentials_path",
    "load_credentials",
    "load_settings",
    "sanitize_api_key",
    "save_credential",
    "settings_path",
]
