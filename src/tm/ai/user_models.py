"""User-defined providers and models loaded from ``<config>/models.toml``.

Lets a user add a provider (any OpenAI-compatible, Anthropic, or Google
endpoint) or extra models on an existing provider without editing the package.

Example::

    # ~/.config/the-machine/models.toml  (Windows: %APPDATA%\\the-machine\\models.toml)

    [providers.myprovider]
    name = "My Provider"
    api = "openai-completions"          # or anthropic-messages / google-generative-ai
    base_url = "https://api.example.com/v1"
    api_key_env = "MYPROVIDER_API_KEY"  # string or list of strings
    default_model = "my-model"

    [[providers.myprovider.models]]
    id = "my-model"
    context_window = 32768
    max_tokens = 4096
    reasoning = false

Adding a ``[[providers.<id>.models]]`` block for a built-in provider appends or
overrides models on it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from tm.ai.catalog import PRESETS, ProviderPreset
from tm.ai.types import Model
from tm.config import read_toml

DEFAULT_CONTEXT_WINDOW = 128_000
DEFAULT_MAX_TOKENS = 8_192


class UserModelError(ValueError):
    """Raised when ``models.toml`` exists but cannot be parsed or is invalid."""


def models_path(config_dir: Path) -> Path:
    return config_dir / "models.toml"


def _as_env(value: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return fallback
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    raise UserModelError(f"api_key_env must be a string or list, got {type(value).__name__}")


def _optional_float(raw: dict, key: str, fallback: float | None) -> float | None:
    value = raw.get(key, fallback)
    return None if value is None else float(value)


def _merge_model(pid: str, raw: dict, existing: Model | None, base_url: str | None) -> Model:
    if "id" not in raw:
        raise UserModelError(f"a model under provider '{pid}' is missing 'id'")
    model_id = str(raw["id"])
    return Model(
        id=model_id,
        provider=pid,
        api=str(raw.get("api") or (existing.api if existing else "openai-completions")),
        base_url=base_url,
        context_window=int(
            raw.get("context_window", existing.context_window if existing else DEFAULT_CONTEXT_WINDOW)
        ),
        max_tokens=int(raw.get("max_tokens", existing.max_tokens if existing else DEFAULT_MAX_TOKENS)),
        reasoning=bool(raw.get("reasoning", existing.reasoning if existing else False)),
        input_cost=_optional_float(raw, "input_cost", existing.input_cost if existing else None),
        output_cost=_optional_float(raw, "output_cost", existing.output_cost if existing else None),
        cache_read_cost=_optional_float(
            raw, "cache_read_cost", existing.cache_read_cost if existing else None
        ),
    )


def _merge_preset(base: ProviderPreset | None, pid: str, spec: dict) -> ProviderPreset:
    name = str(spec.get("name") or (base.name if base else pid))
    api = str(spec.get("api") or (base.api if base else "openai-completions"))
    raw_base_url = spec.get("base_url", base.base_url if base else None)
    base_url = str(raw_base_url) if raw_base_url is not None else None
    api_key_env = _as_env(spec.get("api_key_env"), base.api_key_env if base else ())

    models: dict[str, Model] = {m.id: m for m in (base.models if base else ())}
    for raw in spec.get("models", []):
        if not isinstance(raw, dict):
            raise UserModelError(f"models under provider '{pid}' must be tables")
        model = _merge_model(pid, raw, models.get(str(raw.get("id"))), base_url)
        models[model.id] = model

    default_model = spec.get("default_model") or (base.default_model if base else None)
    if default_model is None and models:
        default_model = next(iter(models))

    return ProviderPreset(
        id=pid,
        name=name,
        api=api,
        base_url=base_url,
        api_key_env=api_key_env,
        models=tuple(models.values()),
        default_model=str(default_model) if default_model else None,
        include_usage=bool(spec.get("include_usage", base.include_usage if base else True)),
    )


def load_presets(config_dir: Path | None) -> dict[str, ProviderPreset]:
    """Built-in presets merged with the user's ``models.toml`` (if present)."""
    presets = dict(PRESETS)
    if config_dir is None:
        return presets
    path = models_path(config_dir)
    if not path.exists():
        return presets
    try:
        data = read_toml(path)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise UserModelError(f"could not read {path}: {exc}") from exc

    providers = data.get("providers", {})
    if not isinstance(providers, dict):
        raise UserModelError(f"{path}: 'providers' must be a table")
    for pid, spec in providers.items():
        if not isinstance(spec, dict):
            raise UserModelError(f"{path}: provider '{pid}' must be a table")
        presets[str(pid)] = _merge_preset(presets.get(str(pid)), str(pid), spec)
    return presets


__all__ = ["UserModelError", "load_presets", "models_path"]
