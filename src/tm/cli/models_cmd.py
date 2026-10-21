"""Interactive setup for local OpenAI-compatible model providers."""

from __future__ import annotations

from pathlib import Path

import tomli_w
import typer
from rich.console import Console

from tm.config import config_dir, read_toml, save_credential

app = typer.Typer(
    add_completion=False,
    help="Manage user-defined model providers (models.toml).",
)
console = Console()

_DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
_DEFAULT_CONTEXT = 32768
_DEFAULT_MAX_TOKENS = 4096


def models_path(directory: Path | None = None) -> Path:
    return (directory or config_dir()) / "models.toml"


def settings_path(directory: Path | None = None) -> Path:
    return (directory or config_dir()) / "settings.toml"


def build_provider(
    provider_id: str,
    *,
    base_url: str,
    model_id: str,
    context_window: int,
    max_tokens: int,
    reasoning: bool,
    api_key_env: str | None = None,
) -> dict:
    spec: dict = {
        "name": provider_id,
        "api": "openai-completions",
        "base_url": base_url,
        "default_model": model_id,
        "models": [
            {
                "id": model_id,
                "context_window": context_window,
                "max_tokens": max_tokens,
                "reasoning": reasoning,
            }
        ],
    }
    if api_key_env:
        spec["api_key_env"] = api_key_env
    return spec


def merge_provider(path: Path, provider_id: str, spec: dict) -> Path:
    """Add a provider, or merge into an existing one (preserving extra fields)."""
    data = read_toml(path) if path.exists() else {}
    providers = data.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    existing = providers.get(provider_id)
    if isinstance(existing, dict):
        merged = dict(existing)
        merged["api"] = spec.get("api", merged.get("api", "openai-completions"))
        merged["base_url"] = spec.get("base_url", merged.get("base_url"))
        merged["default_model"] = spec.get("default_model", merged.get("default_model"))
        if not existing.get("name") or str(existing.get("name")) == provider_id:
            merged["name"] = spec.get("name", provider_id)
        if spec.get("api_key_env"):
            merged["api_key_env"] = spec["api_key_env"]
        else:
            merged.pop("api_key_env", None)

        old_models = [
            dict(item)
            for item in existing.get("models", [])
            if isinstance(item, dict)
        ]
        old_default = str(existing.get("default_model") or "")
        new_models = [
            item for item in spec.get("models", []) if isinstance(item, dict)
        ]
        for new in new_models:
            new_id = str(new.get("id", ""))
            index = next(
                (i for i, item in enumerate(old_models) if str(item.get("id", "")) == new_id),
                None,
            )
            if index is not None:
                old_models[index] = {**old_models[index], **new}
            elif len(old_models) == 1 and str(old_models[0].get("id", "")) == old_default:
                # Renamed the only model: keep its costs / extra fields.
                old_models[0] = {**old_models[0], **new}
            else:
                old_models.append(dict(new))
        merged["models"] = old_models
        providers[provider_id] = merged
    else:
        providers[provider_id] = spec
    data["providers"] = providers
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        tomli_w.dump(data, handle)
    return path


def set_default(path: Path, provider_id: str, model_id: str) -> Path:
    """Set ``provider``/``model`` in ``settings.toml``, preserving other keys."""
    data = read_toml(path) if path.exists() else {}
    data["provider"] = provider_id
    data["model"] = model_id
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        tomli_w.dump(data, handle)
    return path


def _store_key(provider: str, key: str) -> None:
    saved = save_credential(provider, key)
    masked = f"{key[:6]}...{key[-4:]} ({len(key)} chars)"
    console.print(
        f"stored API key for '{provider}' in [bold]{saved}[/bold] ({masked})"
    )


def remove_provider(path: Path, provider_id: str) -> None:
    """Delete a provider from ``models.toml``, preserving the rest."""
    data = read_toml(path) if path.exists() else {}
    providers = data.get("providers")
    if isinstance(providers, dict):
        providers.pop(provider_id, None)
        data["providers"] = providers
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        tomli_w.dump(data, handle)


def clear_default_if_matches(settings_file: Path, provider_id: str) -> bool:
    """Drop the provider/model default from settings.toml if it points here."""
    if not settings_file.exists():
        return False
    data = read_toml(settings_file)
    if data.get("provider") != provider_id:
        return False
    data.pop("provider", None)
    data.pop("model", None)
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    with settings_file.open("wb") as handle:
        tomli_w.dump(data, handle)
    return True


def _existing_provider(path: Path, provider_id: str) -> dict | None:
    if not path.exists():
        return None
    providers = read_toml(path).get("providers")
    if not isinstance(providers, dict):
        return None
    spec = providers.get(provider_id)
    return spec if isinstance(spec, dict) else None


def _default_model_entry(existing: dict | None) -> dict:
    if not existing:
        return {}
    models = [item for item in existing.get("models", []) if isinstance(item, dict)]
    if not models:
        return {}
    default = str(existing.get("default_model") or "")
    for item in models:
        if str(item.get("id", "")) == default:
            return item
    return dict(models[0])


def _configure(
    provider: str,
    *,
    base_url: str | None,
    model: str | None,
    context_window: int | None,
    max_tokens: int | None,
    reasoning: bool,
    api_key_env: str | None,
    api_key: str | None,
    make_default: bool,
    assume_yes: bool,
    require_existing: bool,
) -> None:
    models_file = models_path()
    existing = _existing_provider(models_file, provider)
    if require_existing and existing is None:
        console.print(
            f"[red]provider '{provider}' is not in {models_file}[/red]; "
            "use `tm models add-local`."
        )
        raise typer.Exit(code=1)

    current = _default_model_entry(existing)
    default_base = str((existing or {}).get("base_url") or _DEFAULT_BASE_URL)
    default_model = str(
        (existing or {}).get("default_model") or current.get("id") or ""
    )
    default_context = int(current.get("context_window", _DEFAULT_CONTEXT))
    default_max = int(current.get("max_tokens", _DEFAULT_MAX_TOKENS))
    default_reasoning = bool(current.get("reasoning", reasoning))
    default_key_env = str((existing or {}).get("api_key_env") or "")

    if assume_yes:
        base_url = base_url or default_base
        model = model or default_model
        if not model:
            console.print("[red]--model is required with --yes[/red]")
            raise typer.Exit(code=1)
        context_window = context_window or default_context
        max_tokens = max_tokens or default_max
        if api_key_env is None:
            api_key_env = default_key_env or None
    else:
        if not require_existing:
            # add-local lets you pick/rename the provider; edit-local is fixed
            # to its positional argument so it edits in place rather than
            # silently creating a new provider.
            provider = typer.prompt("Provider id", default=provider)
        base_url = base_url or typer.prompt("Base URL", default=default_base)
        model = model or typer.prompt("Model id (see the server's /v1/models)", default=default_model)
        if context_window is None:
            context_window = typer.prompt(
                "Context window (tokens)", default=default_context, type=int
            )
        if max_tokens is None:
            max_tokens = typer.prompt(
                "Max output tokens", default=default_max, type=int
            )
        reasoning = typer.confirm("Is it a reasoning model?", default=default_reasoning)
        if api_key is None:
            answer = typer.prompt(
                "Paste the API key (blank if the endpoint ignores keys)",
                default="",
                show_default=False,
                hide_input=True,
            ).strip()
            api_key = answer or None
        if api_key_env is None:
            answer = typer.prompt(
                "API key env var (leave blank if none)",
                default=default_key_env,
                show_default=False,
            ).strip()
            api_key_env = answer or None
        make_default = typer.confirm(
            "Set as the default provider/model?", default=make_default
        )

    assert model is not None
    base_url = (base_url or _DEFAULT_BASE_URL).strip()
    if not base_url.startswith(("http://", "https://")):
        console.print("[red]base URL must start with http:// or https://[/red]")
        raise typer.Exit(code=1)
    if context_window <= 0 or max_tokens <= 0:
        console.print("[red]context window and max tokens must be positive[/red]")
        raise typer.Exit(code=1)

    # Re-resolve after any interactive changes so add/edit report and write to
    # the final provider id correctly.
    existing = _existing_provider(models_file, provider)

    if api_key:
        _store_key(provider, api_key)

    spec = build_provider(
        provider,
        base_url=base_url,
        model_id=model,
        context_window=context_window,
        max_tokens=max_tokens,
        reasoning=reasoning,
        api_key_env=api_key_env,
    )
    merge_provider(models_file, provider, spec)
    action = "updated" if existing is not None else "wrote"
    console.print(f"{action} [bold]{models_file}[/bold] (provider '{provider}')")

    if make_default:
        settings_file = set_default(settings_path(), provider, model)
        console.print(
            f"set default in [bold]{settings_file}[/bold]: {provider}/{model}"
        )

    console.print("restart tm, then verify with: [bold]tm --list-models[/bold]")


@app.command("add")
def add_local(
    provider: str = typer.Option("local", "--provider", help="Provider id."),
    base_url: str | None = typer.Option(
        None, "--base-url", help="OpenAI-compatible base URL (usually ends with /v1)."
    ),
    model: str | None = typer.Option(None, "--model", help="Model id served by the endpoint."),
    context_window: int | None = typer.Option(
        None, "--context-window", help="Model context window in tokens."
    ),
    max_tokens: int | None = typer.Option(
        None, "--max-tokens", help="Maximum output tokens."
    ),
    reasoning: bool = typer.Option(False, "--reasoning/--no-reasoning", help="Reasoning model."),
    api_key_env: str | None = typer.Option(
        None, "--api-key-env", help="Env var holding the key (omit for local servers)."
    ),
    api_key: str | None = typer.Option(
        None, "--api-key", help="Store this API key for the provider (overrides env)."
    ),
    make_default: bool = typer.Option(
        True, "--default/--no-default", help="Also set it as the default provider/model."
    ),
    assume_yes: bool = typer.Option(
        False, "--yes", "-y", help="Do not prompt; require --model."
    ),
) -> None:
    """Add a custom OpenAI-compatible model (Ollama, LM Studio, vLLM, a hosted
    endpoint, DeepSeek, ...). Works for both local and remote base URLs.

    Re-running it for an existing provider pre-fills the current values and
    updates them in place, preserving other models and extra fields.
    """
    _configure(
        provider,
        base_url=base_url,
        model=model,
        context_window=context_window,
        max_tokens=max_tokens,
        reasoning=reasoning,
        api_key_env=api_key_env,
        api_key=api_key,
        make_default=make_default,
        assume_yes=assume_yes,
        require_existing=False,
    )


@app.command("edit")
def edit_local(
    provider: str = typer.Argument("local", help="Provider id to edit."),
    base_url: str | None = typer.Option(None, "--base-url", help="New base URL."),
    model: str | None = typer.Option(None, "--model", help="New model id."),
    context_window: int | None = typer.Option(None, "--context-window"),
    max_tokens: int | None = typer.Option(None, "--max-tokens"),
    reasoning: bool = typer.Option(False, "--reasoning/--no-reasoning"),
    api_key_env: str | None = typer.Option(None, "--api-key-env"),
    api_key: str | None = typer.Option(
        None, "--api-key", help="Store this API key for the provider (overrides env)."
    ),
    make_default: bool = typer.Option(True, "--default/--no-default"),
    assume_yes: bool = typer.Option(
        False, "--yes", "-y", help="Do not prompt; keep values not given."
    ),
) -> None:
    """Edit an existing provider, pre-filling its current values."""
    _configure(
        provider,
        base_url=base_url,
        model=model,
        context_window=context_window,
        max_tokens=max_tokens,
        reasoning=reasoning,
        api_key_env=api_key_env,
        api_key=api_key,
        make_default=make_default,
        assume_yes=assume_yes,
        require_existing=True,
    )


@app.command("delete")
def delete_provider(
    provider: str = typer.Argument(..., help="Provider id to remove."),
    force: bool = typer.Option(False, "--force", "-f", help="Do not ask for confirmation."),
) -> None:
    """Delete a provider and clear it as the default if set."""
    path = models_path()
    if _existing_provider(path, provider) is None:
        console.print(f"[red]provider '{provider}' is not in {path}[/red]")
        raise typer.Exit(code=1)
    if not force and not typer.confirm(f"Remove provider '{provider}' from {path}?"):
        console.print("aborted")
        return
    remove_provider(path, provider)
    if clear_default_if_matches(settings_path(), provider):
        console.print(
            f"cleared default in [bold]{settings_path()}[/bold] (was provider '{provider}')"
        )
    console.print(f"removed provider '{provider}' from [bold]{path}[/bold]")


@app.command("list")
def list_local() -> None:
    """List providers defined in models.toml."""
    path = models_path()
    if not path.exists():
        console.print("no models.toml yet (create one with `tm models add`)")
        return
    data = read_toml(path)
    providers = data.get("providers", {})
    if not isinstance(providers, dict) or not providers:
        console.print(f"no providers in {path}")
        return
    for provider_id, spec in providers.items():
        if not isinstance(spec, dict):
            continue
        base_url = spec.get("base_url", "")
        default = spec.get("default_model", "")
        console.print(f"{provider_id} | {base_url} | default={default}")
    console.print(f"[dim]{path}[/dim]")


__all__ = [
    "add_local",
    "app",
    "build_provider",
    "clear_default_if_matches",
    "delete_provider",
    "edit_local",
    "list_local",
    "merge_provider",
    "models_path",
    "remove_provider",
    "set_default",
    "settings_path",
]
