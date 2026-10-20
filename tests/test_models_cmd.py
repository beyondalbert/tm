from __future__ import annotations

from pathlib import Path

from tm.cli.models_cmd import build_provider, merge_provider, set_default
from tm.config import read_toml


def test_build_provider_shape() -> None:
    spec = build_provider(
        "local",
        base_url="http://127.0.0.1:11434/v1",
        model_id="qwen2.5:7b",
        context_window=32768,
        max_tokens=4096,
        reasoning=False,
    )
    assert spec["api"] == "openai-completions"
    assert spec["base_url"] == "http://127.0.0.1:11434/v1"
    assert spec["default_model"] == "qwen2.5:7b"
    assert spec["models"][0]["id"] == "qwen2.5:7b"
    assert spec["models"][0]["context_window"] == 32768
    assert "api_key_env" not in spec


def test_build_provider_with_api_key_env() -> None:
    spec = build_provider(
        "local",
        base_url="http://host/v1",
        model_id="m",
        context_window=1024,
        max_tokens=128,
        reasoning=True,
        api_key_env="LOCAL_KEY",
    )
    assert spec["api_key_env"] == "LOCAL_KEY"
    assert spec["models"][0]["reasoning"] is True


def test_merge_provider_preserves_other_providers(tmp_path: Path) -> None:
    path = tmp_path / "models.toml"
    path.write_text(
        '[providers.other]\napi = "openai-completions"\nbase_url = "http://h/v1"\n',
        encoding="utf-8",
    )
    merge_provider(
        path,
        "local",
        build_provider(
            "local",
            base_url="http://127.0.0.1:11434/v1",
            model_id="m",
            context_window=1024,
            max_tokens=128,
            reasoning=False,
        ),
    )
    data = read_toml(path)
    assert set(data["providers"]) == {"other", "local"}
    assert data["providers"]["local"]["models"][0]["id"] == "m"


def test_set_default_preserves_settings(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text('temperature = 0.2\nauto_compact = false\n', encoding="utf-8")
    set_default(path, "local", "qwen2.5:7b")
    data = read_toml(path)
    assert data["provider"] == "local"
    assert data["model"] == "qwen2.5:7b"
    assert data["temperature"] == 0.2
    assert data["auto_compact"] is False


def test_merge_provider_updates_in_place_and_preserves_model_extras(
    tmp_path: Path,
) -> None:
    path = tmp_path / "models.toml"
    path.write_text(
        """[providers.local]
name = "My Local"
api = "openai-completions"
base_url = "http://old/v1"
default_model = "old"

[[providers.local.models]]
id = "old"
context_window = 1000
max_tokens = 100
input_cost = 0.5
""",
        encoding="utf-8",
    )
    merge_provider(
        path,
        "local",
        build_provider(
            "local",
            base_url="http://new/v1",
            model_id="old",
            context_window=2000,
            max_tokens=200,
            reasoning=False,
        ),
    )
    provider = read_toml(path)["providers"]["local"]
    assert provider["name"] == "My Local"  # custom name preserved
    assert provider["base_url"] == "http://new/v1"
    model = provider["models"][0]
    assert model["context_window"] == 2000
    assert model["max_tokens"] == 200
    assert model["input_cost"] == 0.5  # extra field preserved


def test_merge_provider_renames_a_single_model_keeping_extras(tmp_path: Path) -> None:
    path = tmp_path / "models.toml"
    path.write_text(
        """[providers.local]
api = "openai-completions"
base_url = "http://h/v1"
default_model = "old"

[[providers.local.models]]
id = "old"
context_window = 1000
max_tokens = 100
output_cost = 1.5
""",
        encoding="utf-8",
    )
    merge_provider(
        path,
        "local",
        build_provider(
            "local",
            base_url="http://h/v1",
            model_id="new",
            context_window=4096,
            max_tokens=512,
            reasoning=True,
        ),
    )
    provider = read_toml(path)["providers"]["local"]
    assert provider["default_model"] == "new"
    model = provider["models"][0]
    assert model["id"] == "new"
    assert model["output_cost"] == 1.5
    assert model["reasoning"] is True

