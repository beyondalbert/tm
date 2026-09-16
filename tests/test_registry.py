import pytest

from tm.ai.catalog import PRESETS
from tm.ai.registry import Registry, RegistryError


def test_resolve_default_provider_and_model() -> None:
    registry = Registry()
    provider, model = registry.resolve(None, "deepseek")
    assert provider.id == "deepseek"
    assert model.id == "deepseek-chat"


def test_resolve_provider_slash_model() -> None:
    registry = Registry()
    provider, model = registry.resolve("qwen/qwen-max", None)
    assert provider.id == "qwen"
    assert model.id == "qwen-max"


def test_resolve_model_without_provider_searches_catalog() -> None:
    registry = Registry()
    provider, model = registry.resolve("deepseek-reasoner", None)
    assert provider.id == "deepseek"
    assert model.id == "deepseek-reasoner"


def test_resolve_unknown_model_raises() -> None:
    registry = Registry()
    with pytest.raises(RegistryError):
        registry.resolve("does-not-exist-xyz", None)


def test_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    registry = Registry()
    assert registry.has_credentials(PRESETS["deepseek"])
    provider = registry.provider("deepseek")
    assert provider.api_key == "sk-test"


def test_explicit_api_key_override() -> None:
    registry = Registry(api_keys={"deepseek": "sk-explicit"})
    assert registry.provider("deepseek").api_key == "sk-explicit"


def test_provider_without_key_env_has_credentials() -> None:
    registry = Registry()
    assert registry.has_credentials(PRESETS["ollama"])


def test_unknown_provider_raises() -> None:
    registry = Registry()
    with pytest.raises(RegistryError):
        registry.provider("nope")


def test_domestic_providers_present() -> None:
    for provider_id in ("deepseek", "qwen", "moonshot", "zhipu"):
        assert provider_id in PRESETS
        assert PRESETS[provider_id].base_url


def test_anthropic_provider_built() -> None:
    from tm.ai.providers.anthropic import AnthropicProvider

    registry = Registry()
    provider = registry.provider("anthropic")
    assert isinstance(provider, AnthropicProvider)
    assert provider.id == "anthropic"


def test_google_provider_built() -> None:
    from tm.ai.providers.google import GoogleProvider

    registry = Registry()
    provider = registry.provider("google")
    assert isinstance(provider, GoogleProvider)
    assert provider.id == "google"


def test_extra_openai_compatible_providers_present() -> None:
    for provider_id in ("siliconflow", "groq", "openrouter", "together", "xai"):
        assert provider_id in PRESETS
        assert PRESETS[provider_id].base_url
