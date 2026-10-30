"""Configuration loading for The Machine."""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Literal

import tomli_w

from tm.compat import TOMLDecodeError, load_toml, loads_toml


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


def read_toml(path: Path) -> dict:
    """Parse a TOML file, tolerating a UTF-8 BOM (common from Windows editors)."""
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    parsed = loads_toml(data.decode("utf-8"))
    if not isinstance(parsed, dict):  # toml parsers always return a dict; guard for mypy
        return {}
    return parsed


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
    #: Recent tokens kept verbatim after compaction (0 = use compact_keep_recent).
    compact_keep_tokens: int = 20_000
    #: Absolute token budget for the compaction trigger; 0 uses the window ratio only.
    compact_max_tokens: int = 200_000
    #: Per-model overrides, keyed by "provider/model". See CompactionLimits.
    model_overrides: dict[str, dict[str, object]] = field(default_factory=dict)
    #: Rewrite old tool output in the request view (the session keeps it verbatim).
    prune_tool_output: bool = True
    #: Tool results from the last N assistant turns are always kept verbatim.
    prune_tool_output_keep_turns: int = 8
    #: Only results at least this many characters long are rewritten.
    prune_tool_output_min_chars: int = 4000
    #: Characters of the original kept in the rewritten stub (its tail).
    prune_tool_output_keep_chars: int = 400
    #: Where rewritten full output is saved (default: <config>/workspace/spill).
    spill_dir: str = ""
    #: Answer a repeated, unchanged read of the same file/range with a short note.
    read_dedupe: bool = True
    max_turns: int = 300
    max_retries: int = 3
    language: str = "auto"
    telemetry: bool = False
    durable: bool = False
    mouse: bool = True
    default_project_trust: str = "ask"
    cache_retention: str = "short"
    env_probe: bool = True
    python_executable: str | None = None
    python_timeout: int = 120
    shell_timeout: int = 120
    workspace_dir: str | None = None
    auto_install: bool = True


def load_settings() -> Settings:
    path = settings_path()
    settings = Settings()
    if not path.exists():
        return settings
    data = read_toml(path)
    known = {f.name for f in fields(Settings)}
    for key, value in data.items():
        if key in known:
            setattr(settings, key, value)
    return settings


@dataclass(frozen=True)
class CompactionLimits:
    """Effective compaction settings for one model."""

    threshold: float
    keep_recent: int
    keep_tokens: int
    max_tokens: int


_OVERRIDE_FIELDS = (
    "compact_threshold",
    "compact_keep_recent",
    "compact_keep_tokens",
    "compact_max_tokens",
)


def _override_int(value: object, key: str, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"model_overrides[{key!r}].{name}: expected an integer")
    if value < minimum:
        raise ValueError(
            f"model_overrides[{key!r}].{name}: must be at least {minimum}"
        )
    return value


def _override_threshold(value: object, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"model_overrides[{key!r}].compact_threshold: expected a number")
    threshold = float(value)
    if not 0 < threshold <= 1:
        raise ValueError(
            f"model_overrides[{key!r}].compact_threshold: must be between 0 and 1"
        )
    return threshold


def compaction_for(settings: Settings, provider: str, model_id: str) -> CompactionLimits:
    """Resolve the global compaction settings and the model's overrides.

    Overrides live under ``[model_overrides."provider/model"]`` in settings.toml:

        [model_overrides."local-deepseek/deepseek-v4-flash-0731"]
        compact_max_tokens = 100000
        compact_keep_tokens = 15000
    """
    limits = CompactionLimits(
        threshold=settings.compact_threshold,
        keep_recent=settings.compact_keep_recent,
        keep_tokens=settings.compact_keep_tokens,
        max_tokens=settings.compact_max_tokens,
    )
    if not isinstance(settings.model_overrides, dict):
        raise ValueError("model_overrides: expected a table")
    key = f"{provider}/{model_id}"
    raw = settings.model_overrides.get(key)
    if raw is None:
        return limits
    if not isinstance(raw, dict):
        raise ValueError(f"model_overrides[{key!r}]: expected a table")
    unknown = set(raw) - set(_OVERRIDE_FIELDS)
    if unknown:
        names = ", ".join(sorted(str(name) for name in unknown))
        raise ValueError(f"model_overrides[{key!r}]: unknown field(s): {names}")
    threshold = limits.threshold
    keep_recent = limits.keep_recent
    keep_tokens = limits.keep_tokens
    max_tokens = limits.max_tokens
    if "compact_threshold" in raw:
        threshold = _override_threshold(raw["compact_threshold"], key)
    if "compact_keep_recent" in raw:
        keep_recent = _override_int(raw["compact_keep_recent"], key, "compact_keep_recent", 1)
    if "compact_keep_tokens" in raw:
        keep_tokens = _override_int(raw["compact_keep_tokens"], key, "compact_keep_tokens", 0)
    if "compact_max_tokens" in raw:
        max_tokens = _override_int(raw["compact_max_tokens"], key, "compact_max_tokens", 0)
    return CompactionLimits(
        threshold=threshold,
        keep_recent=keep_recent,
        keep_tokens=keep_tokens,
        max_tokens=max_tokens,
    )


SettingKind = Literal["bool", "int", "float", "str", "choice"]


@dataclass(frozen=True)
class SettingField:
    """One settings.toml field that an interactive editor may change."""

    name: str
    kind: SettingKind
    choices: tuple[str, ...] = ()
    nullable: bool = False
    minimum: float | None = None
    maximum: float | None = None


#: Settings exposed by ``/settings``. Curated to values that are safe to change
#: at runtime; provider/model changes stay on ``/model``.
SETTING_FIELDS: tuple[SettingField, ...] = (
    SettingField("auto_compact", "bool"),
    SettingField("compact_threshold", "float", minimum=0.01, maximum=1.0),
    SettingField("compact_keep_recent", "int", minimum=1),
    SettingField("compact_keep_tokens", "int", minimum=0),
    SettingField("compact_max_tokens", "int", minimum=0),
    SettingField("prune_tool_output", "bool"),
    SettingField("prune_tool_output_keep_turns", "int", minimum=1),
    SettingField("prune_tool_output_min_chars", "int", minimum=0),
    SettingField("prune_tool_output_keep_chars", "int", minimum=0),
    SettingField("spill_dir", "str"),
    SettingField("read_dedupe", "bool"),
    SettingField("max_turns", "int", minimum=1),
    SettingField("max_retries", "int", minimum=0),
    SettingField("temperature", "float", nullable=True),
    SettingField("language", "choice", choices=("en", "zh", "auto")),
    SettingField("env_probe", "bool"),
)

_TRUE_VALUES = frozenset(("1", "true", "yes", "on", "y", "t"))
_FALSE_VALUES = frozenset(("0", "false", "no", "off", "n", "f"))


def format_setting_value(field: SettingField, value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def parse_setting_value(field: SettingField, raw: str) -> object:
    """Parse and validate one editor value; raises ``ValueError`` on bad input."""
    text = raw.strip()
    if field.kind == "bool":
        lowered = text.lower()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
        raise ValueError(f"{field.name}: expected true or false")
    if field.nullable and not text:
        return None
    if field.kind == "choice":
        lowered = text.lower()
        if lowered not in field.choices:
            raise ValueError(
                f"{field.name}: expected one of {', '.join(field.choices)}"
            )
        return lowered
    if field.kind in ("int", "float"):
        try:
            number: float | int = int(text) if field.kind == "int" else float(text)
        except ValueError as error:
            raise ValueError(f"{field.name}: expected a {field.kind}") from error
        if field.minimum is not None and number < field.minimum:
            raise ValueError(f"{field.name}: must be at least {field.minimum:g}")
        if field.maximum is not None and number > field.maximum:
            raise ValueError(f"{field.name}: must be at most {field.maximum:g}")
        return number
    return text


def save_settings_values(values: dict[str, object]) -> Path:
    """Merge changed values into settings.toml, preserving unmanaged keys."""
    path = settings_path()
    data: dict = read_toml(path) if path.exists() else {}
    for key, value in values.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        tomli_w.dump(data, handle)
    return path


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
        data = read_toml(path)
    except (OSError, TOMLDecodeError):
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
                data = load_toml(handle)
        except (OSError, TOMLDecodeError):
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
    "CompactionLimits",
    "Settings",
    "compaction_for",
    "config_dir",
    "credentials_path",
    "load_credentials",
    "load_settings",
    "read_toml",
    "sanitize_api_key",
    "save_credential",
    "settings_path",
]
