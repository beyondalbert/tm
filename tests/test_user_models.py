"""Tests for user-defined providers/models loaded from models.toml."""

from __future__ import annotations

from pathlib import Path

import pytest

from tm.ai.catalog import PRESETS
from tm.ai.registry import Registry
from tm.ai.user_models import UserModelError, load_presets, models_path

CUSTOM = """
[providers.myprovider]
name = "My Provider"
api = "openai-completions"
base_url = "https://api.example.com/v1"
api_key_env = "MYPROVIDER_API_KEY"
default_model = "my-model"

[[providers.myprovider.models]]
id = "my-model"
context_window = 32768
max_tokens = 4096
reasoning = true
input_cost = 0.5
output_cost = 1.5
cache_read_cost = 0.05

[[providers.myprovider.models]]
id = "my-small"
"""


def _write(tmp_path: Path, text: str) -> Path:
    (tmp_path / "models.toml").write_text(text, encoding="utf-8")
    return tmp_path


def test_models_path(tmp_path: Path) -> None:
    assert models_path(tmp_path) == tmp_path / "models.toml"


def test_without_file_returns_builtins(tmp_path: Path) -> None:
    presets = load_presets(tmp_path)
    assert set(presets) == set(PRESETS)


def test_load_presets_none_returns_builtins() -> None:
    assert set(load_presets(None)) == set(PRESETS)


def test_custom_provider_is_loaded(tmp_path: Path) -> None:
    presets = load_presets(_write(tmp_path, CUSTOM))
    assert "myprovider" in presets
    preset = presets["myprovider"]
    assert preset.name == "My Provider"
    assert preset.base_url == "https://api.example.com/v1"
    assert preset.api_key_env == ("MYPROVIDER_API_KEY",)
    assert preset.default_model == "my-model"
    ids = [model.id for model in preset.models]
    assert ids == ["my-model", "my-small"]
    assert preset.models[0].context_window == 32768
    assert preset.models[0].reasoning is True
    assert preset.models[0].input_cost == 0.5
    assert preset.models[0].cache_read_cost == 0.05
    # defaults applied to the model without explicit values
    assert preset.models[1].context_window == 128_000


def test_registry_resolves_custom_provider(tmp_path: Path) -> None:
    presets = load_presets(_write(tmp_path, CUSTOM))
    registry = Registry(presets=presets)
    provider, model = registry.resolve("my-model", None)
    assert provider.id == "myprovider"
    assert model.id == "my-model"
    assert model.context_window == 32768


def test_custom_api_key_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYPROVIDER_API_KEY", "sk-custom")
    presets = load_presets(_write(tmp_path, CUSTOM))
    registry = Registry(presets=presets)
    assert registry.has_credentials(presets["myprovider"])
    assert registry.provider("myprovider").api_key == "sk-custom"


def test_add_model_to_builtin_provider(tmp_path: Path) -> None:
    text = """
    [[providers.deepseek.models]]
    id = "deepseek-v4-lite"
    context_window = 65536
    """
    presets = load_presets(_write(tmp_path, text))
    ids = [model.id for model in presets["deepseek"].models]
    assert "deepseek-v4-pro" in ids  # built-ins preserved
    assert "deepseek-v4-lite" in ids


def test_override_builtin_model(tmp_path: Path) -> None:
    text = """
    [[providers.deepseek.models]]
    id = "deepseek-v4-pro"
    context_window = 200000
    """
    presets = load_presets(_write(tmp_path, text))
    model = next(m for m in presets["deepseek"].models if m.id == "deepseek-v4-pro")
    assert model.context_window == 200000
    # not duplicated
    assert sum(1 for m in presets["deepseek"].models if m.id == "deepseek-v4-pro") == 1


def test_invalid_toml_raises(tmp_path: Path) -> None:
    _write(tmp_path, "this is not = = toml")
    with pytest.raises(UserModelError):
        load_presets(tmp_path)


def test_bom_is_tolerated(tmp_path: Path) -> None:
    (tmp_path / "models.toml").write_bytes(b"\xef\xbb\xbf" + CUSTOM.encode("utf-8"))
    presets = load_presets(tmp_path)
    assert "myprovider" in presets


def test_model_without_id_raises(tmp_path: Path) -> None:
    _write(tmp_path, "[providers.p]\n[[providers.p.models]]\ncontext_window = 1000\n")
    with pytest.raises(UserModelError):
        load_presets(tmp_path)
