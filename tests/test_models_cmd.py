from __future__ import annotations

from pathlib import Path

import pytest

from tm.ai.catalog import ProviderPreset
from tm.ai.types import Model
from tm.cli.models_cmd import (
    _extract_model_ids,
    build_provider,
    clear_default_if_matches,
    fetch_model_ids,
    merge_provider,
    remove_provider,
    set_default,
    sync_provider,
)
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



def _sync_preset(*models: Model) -> ProviderPreset:
    return ProviderPreset(
        id="deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        api_key_env=("DEEPSEEK_API_KEY",),
        models=models,
        default_model=models[0].id if models else None,
    )


def test_extract_model_ids_openai_and_google_shapes() -> None:
    assert _extract_model_ids({"data": [{"id": "a"}, {"id": "b"}, {"id": "a"}]}) == [
        "a",
        "b",
    ]
    assert _extract_model_ids({"models": ["x", {"name": "models/y"}]}) == ["x", "y"]
    google = {
        "models": [
            {"name": "models/gemini-chat", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/embedding", "supportedGenerationMethods": ["embedContent"]},
        ]
    }
    assert _extract_model_ids(google, google=True) == ["gemini-chat"]


def test_fetch_model_ids_openai_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_request(url: str, headers: dict[str, str], timeout: float) -> dict:
        seen.update(url=url, headers=headers, timeout=timeout)
        return {"data": [{"id": "one"}, {"id": "two"}]}

    monkeypatch.setattr("tm.cli.models_cmd._request_json", fake_request)
    preset = ProviderPreset(
        id="p",
        name="P",
        base_url="https://api.example.com/v1",
        models=(Model(id="old", provider="p"),),
    )
    assert fetch_model_ids(preset, "sk-test", timeout=5) == ["one", "two"]
    assert seen["url"] == "https://api.example.com/v1/models"
    assert seen["headers"]["Authorization"] == "Bearer sk-test"
    assert seen["timeout"] == 5


def test_sync_provider_mirrors_server_and_preserves_metadata(tmp_path: Path) -> None:
    path = tmp_path / "models.toml"
    path.write_text(
        """[providers.deepseek]
api = "openai-completions"
base_url = "https://api.deepseek.com/v1"
default_model = "removed"

[[providers.deepseek.models]]
id = "removed"
context_window = 1000

[[providers.deepseek.models]]
id = "kept"
context_window = 4096
max_tokens = 512
input_cost = 0.5
custom_field = "keep-me"
""",
        encoding="utf-8",
    )
    preset = _sync_preset(
        Model(id="kept", provider="deepseek", context_window=4096, max_tokens=512),
        Model(id="fresh", provider="deepseek", context_window=200000, max_tokens=8192),
    )
    sync_provider(path, "deepseek", ["fresh", "kept"], preset=preset)

    provider = read_toml(path)["providers"]["deepseek"]
    assert provider["models_mode"] == "replace"
    assert provider["default_model"] == "kept"
    assert [item["id"] for item in provider["models"]] == ["fresh", "kept"]
    kept = provider["models"][1]
    assert kept["context_window"] == 4096
    assert kept["input_cost"] == 0.5
    assert kept["custom_field"] == "keep-me"
    assert provider["models"][0]["context_window"] == 200000


def test_sync_provider_append_keeps_old_models(tmp_path: Path) -> None:
    path = tmp_path / "models.toml"
    path.write_text(
        """[providers.deepseek]
default_model = "old"
models_mode = "replace"

[[providers.deepseek.models]]
id = "old"
""",
        encoding="utf-8",
    )
    sync_provider(
        path,
        "deepseek",
        ["fresh"],
        preset=_sync_preset(Model(id="fresh", provider="deepseek")),
        append=True,
    )

    provider = read_toml(path)["providers"]["deepseek"]
    assert "models_mode" not in provider
    assert provider["default_model"] == "old"
    assert [item["id"] for item in provider["models"]] == ["fresh", "old"]


def test_sync_provider_uses_default_when_present(tmp_path: Path) -> None:
    path = tmp_path / "models.toml"
    path.write_text(
        "[providers.deepseek]\ndefault_model = \"b\"\n", encoding="utf-8"
    )
    sync_provider(
        path,
        "deepseek",
        ["a", "b"],
        preset=_sync_preset(
            Model(id="a", provider="deepseek"), Model(id="b", provider="deepseek")
        ),
    )
    assert read_toml(path)["providers"]["deepseek"]["default_model"] == "b"


def test_remove_provider_keeps_others(tmp_path: Path) -> None:
    path = tmp_path / "models.toml"
    path.write_text(
        '[providers.other]\napi = "openai-completions"\nbase_url = "http://h/v1"\n'
        '[providers.local]\napi = "openai-completions"\nbase_url = "http://h2/v1"\n',
        encoding="utf-8",
    )
    remove_provider(path, "local")
    providers = read_toml(path)["providers"]
    assert "local" not in providers
    assert "other" in providers


def test_clear_default_when_matching(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text('provider = "local"\nmodel = "m"\n', encoding="utf-8")
    assert clear_default_if_matches(path, "local") is True
    data = read_toml(path)
    assert "provider" not in data
    assert "model" not in data


def test_clear_default_noop_when_not_matching(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text('provider = "other"\nmodel = "m"\n', encoding="utf-8")
    assert clear_default_if_matches(path, "local") is False
    assert read_toml(path)["provider"] == "other"
