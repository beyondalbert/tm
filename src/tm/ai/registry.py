"""Provider registry: resolve provider/model, read API keys from the environment."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterable

from tm.ai.catalog import PRESETS, ProviderPreset
from tm.ai.providers.anthropic import AnthropicProvider
from tm.ai.providers.base import Provider
from tm.ai.providers.google import GoogleProvider
from tm.ai.providers.openai_compat import OpenAICompatProvider
from tm.ai.types import Model


class RegistryError(Exception):
    pass


class Registry:
    def __init__(
        self,
        presets: dict[str, ProviderPreset] | None = None,
        api_keys: dict[str, str] | None = None,
    ) -> None:
        self._presets = dict(presets or PRESETS)
        self._api_keys = dict(api_keys or {})
        self._providers: dict[str, Provider] = {}

    def presets(self) -> list[ProviderPreset]:
        return list(self._presets.values())

    def _api_key(self, preset: ProviderPreset) -> str | None:
        if preset.id in self._api_keys:
            return self._api_keys[preset.id]
        for env in preset.api_key_env:
            value = os.environ.get(env)
            if value:
                return value
        return None

    def has_credentials(self, preset: ProviderPreset) -> bool:
        return bool(preset.api_key_env == ()) or self._api_key(preset) is not None

    def provider(self, provider_id: str) -> Provider:
        if provider_id not in self._providers:
            preset = self._presets.get(provider_id)
            if preset is None:
                raise RegistryError(f"Unknown provider: {provider_id}")
            if preset.api == "openai-completions":
                self._providers[provider_id] = OpenAICompatProvider(
                    preset.id,
                    preset.name,
                    list(preset.models),
                    api_key=self._api_key(preset),
                    base_url=preset.base_url,
                    include_usage=preset.include_usage,
                )
            elif preset.api == "anthropic-messages":
                self._providers[provider_id] = AnthropicProvider(
                    preset.id,
                    preset.name,
                    list(preset.models),
                    api_key=self._api_key(preset),
                    base_url=preset.base_url,
                )
            elif preset.api == "google-generative-ai":
                self._providers[provider_id] = GoogleProvider(
                    preset.id,
                    preset.name,
                    list(preset.models),
                    api_key=self._api_key(preset),
                    base_url=preset.base_url,
                )
            else:
                raise RegistryError(
                    f"Provider '{provider_id}' uses api '{preset.api}', which is not "
                    "supported yet"
                )
        return self._providers[provider_id]

    def models(self) -> list[Model]:
        result: list[Model] = []
        for preset in self._presets.values():
            result.extend(preset.models)
        return result

    def search_models(self, pattern: str) -> list[Model]:
        needle = pattern.lower()
        return [
            m
            for m in self.models()
            if needle in m.id.lower() or needle in m.provider.lower()
        ]

    def _provider_for_model(self, model_id: str) -> Provider | None:
        for preset in self._presets.values():
            for model in preset.models:
                if model.id == model_id:
                    return self.provider(preset.id)
        return None

    def resolve(
        self, model_pattern: str | None = None, provider_id: str | None = None
    ) -> tuple[Provider, Model]:
        if model_pattern and "/" in model_pattern and provider_id is None:
            provider_id, model_pattern = model_pattern.split("/", 1)

        if provider_id is None:
            if not model_pattern:
                provider_id = "deepseek"
            else:
                provider = self._provider_for_model(model_pattern)
                if provider is not None:
                    model = next(
                        m for m in provider.list_models() if m.id == model_pattern
                    )
                    return provider, model
                matches = self.search_models(model_pattern)
                if not matches:
                    raise RegistryError(f"No model matching '{model_pattern}'")
                model = matches[0]
                return self.provider(model.provider), model

        preset = self._presets.get(provider_id)
        if preset is None:
            raise RegistryError(f"Unknown provider: {provider_id}")

        if model_pattern:
            for model in preset.models:
                if model.id == model_pattern:
                    return self.provider(preset.id), model
            for model in preset.models:
                if model_pattern.lower() in model.id.lower():
                    return self.provider(preset.id), model
            return self.provider(preset.id), Model(
                id=model_pattern, provider=preset.id, base_url=preset.base_url
            )

        default_id = preset.default_model or (preset.models[0].id if preset.models else None)
        if default_id is None:
            raise RegistryError(f"No default model configured for '{provider_id}'")
        for model in preset.models:
            if model.id == default_id:
                return self.provider(preset.id), model
        return self.provider(preset.id), Model(id=default_id, provider=preset.id)

    async def aclose(self) -> None:
        for provider in self._providers.values():
            # Closing a client may touch an event loop that is already gone (for
            # example when the process is shutting down); never let that mask the run.
            with contextlib.suppress(Exception):
                await provider.aclose()
        self._providers.clear()


def available_provider_ids(
    registry: Registry, presets: Iterable[ProviderPreset] | None = None
) -> list[str]:
    source = list(presets) if presets is not None else registry.presets()
    return [p.id for p in source if registry.has_credentials(p)]


__all__ = ["Registry", "RegistryError", "available_provider_ids"]
