"""Built-in provider catalog. Domestic providers first, then OpenAI/compatible."""

from __future__ import annotations

from dataclasses import dataclass

from tm.ai.types import Model


@dataclass(frozen=True)
class ProviderPreset:
    id: str
    name: str
    api: str = "openai-completions"
    base_url: str | None = None
    api_key_env: tuple[str, ...] = ()
    models: tuple[Model, ...] = ()
    default_model: str | None = None
    include_usage: bool = True


def _m(provider: str, model_id: str, *, context: int, max_tokens: int, reasoning: bool = False) -> Model:
    return Model(
        id=model_id,
        provider=provider,
        context_window=context,
        max_tokens=max_tokens,
        reasoning=reasoning,
    )


PRESETS: dict[str, ProviderPreset] = {
    # --- Domestic providers first -----------------------------------------
    "deepseek": ProviderPreset(
        id="deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        api_key_env=("DEEPSEEK_API_KEY",),
        default_model="deepseek-chat",
        models=(
            _m("deepseek", "deepseek-chat", context=131_072, max_tokens=8_192),
            _m("deepseek", "deepseek-reasoner", context=131_072, max_tokens=8_192, reasoning=True),
        ),
    ),
    "qwen": ProviderPreset(
        id="qwen",
        name="Qwen (DashScope)",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env=("DASHSCOPE_API_KEY", "QWEN_API_KEY"),
        default_model="qwen-plus",
        models=(
            _m("qwen", "qwen-max", context=131_072, max_tokens=8_192),
            _m("qwen", "qwen-plus", context=131_072, max_tokens=8_192),
            _m("qwen", "qwen-turbo", context=131_072, max_tokens=8_192),
        ),
    ),
    "moonshot": ProviderPreset(
        id="moonshot",
        name="Moonshot (Kimi)",
        base_url="https://api.moonshot.cn/v1",
        api_key_env=("MOONSHOT_API_KEY", "KIMI_API_KEY"),
        default_model="moonshot-v1-32k",
        models=(
            _m("moonshot", "moonshot-v1-8k", context=8_192, max_tokens=4_096),
            _m("moonshot", "moonshot-v1-32k", context=32_768, max_tokens=8_192),
            _m("moonshot", "moonshot-v1-128k", context=131_072, max_tokens=8_192),
            _m("moonshot", "kimi-latest", context=131_072, max_tokens=8_192),
        ),
    ),
    "zhipu": ProviderPreset(
        id="zhipu",
        name="Zhipu (GLM)",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key_env=("ZHIPUAI_API_KEY", "GLM_API_KEY"),
        default_model="glm-4-plus",
        models=(
            _m("zhipu", "glm-4-plus", context=131_072, max_tokens=8_192),
            _m("zhipu", "glm-4-air", context=131_072, max_tokens=8_192),
            _m("zhipu", "glm-4-flash", context=131_072, max_tokens=8_192),
        ),
    ),
    # --- General / local --------------------------------------------------
    "anthropic": ProviderPreset(
        id="anthropic",
        name="Anthropic",
        api="anthropic-messages",
        base_url=None,
        api_key_env=("ANTHROPIC_API_KEY",),
        default_model="claude-3-5-sonnet-latest",
        models=(
            _m("anthropic", "claude-3-5-sonnet-latest", context=200_000, max_tokens=8_192),
            _m("anthropic", "claude-3-5-haiku-latest", context=200_000, max_tokens=8_192),
            _m("anthropic", "claude-3-7-sonnet-latest", context=200_000, max_tokens=8_192),
        ),
    ),
    "google": ProviderPreset(
        id="google",
        name="Google Gemini",
        api="google-generative-ai",
        base_url=None,
        api_key_env=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        default_model="gemini-2.0-flash",
        models=(
            _m("google", "gemini-2.0-flash", context=1_048_576, max_tokens=8_192),
            _m("google", "gemini-2.5-pro", context=1_048_576, max_tokens=8_192, reasoning=True),
        ),
    ),
    "openai": ProviderPreset(
        id="openai",
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        api_key_env=("OPENAI_API_KEY",),
        default_model="gpt-4o",
        models=(
            _m("openai", "gpt-4o", context=128_000, max_tokens=8_192),
            _m("openai", "gpt-4o-mini", context=128_000, max_tokens=8_192),
        ),
    ),
    "siliconflow": ProviderPreset(
        id="siliconflow",
        name="SiliconFlow",
        base_url="https://api.siliconflow.cn/v1",
        api_key_env=("SILICONFLOW_API_KEY",),
        default_model="deepseek-ai/DeepSeek-V3",
        models=(
            _m("siliconflow", "deepseek-ai/DeepSeek-V3", context=65_536, max_tokens=8_192),
            _m("siliconflow", "Qwen/Qwen2.5-72B-Instruct", context=32_768, max_tokens=8_192),
        ),
    ),
    "groq": ProviderPreset(
        id="groq",
        name="Groq",
        base_url="https://api.groq.com/openai/v1",
        api_key_env=("GROQ_API_KEY",),
        default_model="llama-3.3-70b-versatile",
        models=(
            _m("groq", "llama-3.3-70b-versatile", context=128_000, max_tokens=8_192),
            _m("groq", "llama-3.1-8b-instant", context=128_000, max_tokens=8_192),
        ),
    ),
    "openrouter": ProviderPreset(
        id="openrouter",
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env=("OPENROUTER_API_KEY",),
        default_model="anthropic/claude-3.5-sonnet",
        models=(
            _m("openrouter", "anthropic/claude-3.5-sonnet", context=200_000, max_tokens=8_192),
            _m("openrouter", "openai/gpt-4o", context=128_000, max_tokens=8_192),
        ),
    ),
    "together": ProviderPreset(
        id="together",
        name="Together AI",
        base_url="https://api.together.xyz/v1",
        api_key_env=("TOGETHER_API_KEY",),
        default_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        models=(
            _m(
                "together",
                "meta-llama/Llama-3.3-70B-Instruct-Turbo",
                context=131_072,
                max_tokens=8_192,
            ),
        ),
    ),
    "xai": ProviderPreset(
        id="xai",
        name="xAI",
        base_url="https://api.x.ai/v1",
        api_key_env=("XAI_API_KEY",),
        default_model="grok-2-latest",
        models=(
            _m("xai", "grok-2-latest", context=131_072, max_tokens=8_192),
            _m("xai", "grok-2-mini", context=131_072, max_tokens=8_192),
        ),
    ),
    "ollama": ProviderPreset(
        id="ollama",
        name="Ollama (local)",
        base_url="http://localhost:11434/v1",
        api_key_env=(),
        default_model="qwen2.5:7b",
        models=(
            _m("ollama", "qwen2.5:7b", context=32_768, max_tokens=4_096),
            _m("ollama", "llama3.1:8b", context=131_072, max_tokens=4_096),
        ),
    ),
}

DOMESTIC_PROVIDERS = ("deepseek", "qwen", "moonshot", "zhipu")

__all__ = ["DOMESTIC_PROVIDERS", "PRESETS", "ProviderPreset"]
