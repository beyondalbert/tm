from __future__ import annotations

import pytest

from tm.ai.registry import Registry, RegistryError


def test_set_api_key_makes_the_provider_usable() -> None:
    registry = Registry(api_keys={})
    assert registry.provider_key_present("deepseek") is False

    retired = registry.set_api_key("deepseek", "sk-test-123456")
    assert retired is None
    assert registry.provider_key_present("deepseek") is True

    provider = registry.provider("deepseek")
    assert provider.api_key == "sk-test-123456"


def test_setting_a_key_again_returns_the_cached_provider() -> None:
    registry = Registry(api_keys={})
    first = registry.provider("deepseek")
    retired = registry.set_api_key("deepseek", "sk-second")
    assert retired is first
    assert registry.provider("deepseek") is not first
    assert registry.provider("deepseek").api_key == "sk-second"


def test_clear_api_key_forgets_the_injected_value() -> None:
    registry = Registry(api_keys={"deepseek": "sk-stored"})
    assert registry.provider_key_present("deepseek") is True
    registry.clear_api_key("deepseek")
    assert registry.provider_key_present("deepseek") is False


def test_unknown_provider_is_rejected() -> None:
    registry = Registry(api_keys={})
    with pytest.raises(RegistryError):
        registry.set_api_key("nope", "sk-x")
    with pytest.raises(RegistryError):
        registry.clear_api_key("nope")
