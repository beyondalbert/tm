"""``tm serve``: expose TM over JSON-RPC on stdio so another tool can drive it.

Design: ``docs/design/embedding.md``. stdout carries the protocol only, so all
diagnostics go to stderr.

``build_host`` is shared with ``tm mcp-serve`` and ``tm acp``: same policy, same
tool selection, same session store, different protocol adapter.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import typer
from rich.console import Console

from tm.ai.registry import Registry, RegistryError, is_localhost_url
from tm.cli.commands import apply_compaction_settings
from tm.cli.main import (
    Resources,
    _build_agent,
    _load_resources,
    _make_registry,
    _make_store,
    _make_telemetry,
    _mask_key,
    _resolve_trust,
    _restore_session_model,
    _select_tools,
)
from tm.config import (
    config_dir,
    delete_credential,
    load_settings,
    sanitize_api_key,
    save_credential,
)
from tm.core.session import Session, SessionManager
from tm.i18n import set_language
from tm.permissions import (
    READ_TOOLS,
    Approver,
    AutoAllowApprover,
    AutoDenyApprover,
)
from tm.rpc.approver import RpcApprover
from tm.rpc.host import BuiltSession, ServerHost, SessionOptions, SessionRuntime, Wire
from tm.rpc.protocol import (
    INVALID_PARAMS,
    NOT_CONFIGURED,
    UNSUPPORTED,
    RpcError,
    optional_bool,
    optional_str,
    require_str,
)
from tm.rpc.server import HandlerFn, RpcServer
from tm.safety import Journal

app = typer.Typer(
    add_completion=False,
    help="Serve TM over JSON-RPC (stdio) for embedding.",
)
err = Console(stderr=True)


@dataclass
class EmbeddingOptions:
    mode: str = "policy"
    cwd: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    read_only: bool = False
    no_python: bool = False
    no_tools: bool = False
    no_session: bool = False
    no_context_files: bool = False
    no_extensions: bool = False
    dry_run: bool = False
    approve: bool = False
    no_approve: bool = False
    approval_timeout: int = 120_000
    max_turns: Optional[int] = None
    telemetry: bool = False
    durable: bool = False
    max_sessions: int = 4
    approval_style: str = "rpc"
    allow_host_credentials: bool = False
    config_dir: Optional[str] = None
    extra: Dict[str, object] = field(default_factory=dict)


def build_host(options: EmbeddingOptions) -> tuple[ServerHost, Registry]:
    """Build the server host shared by serve, MCP, and ACP."""
    if options.config_dir:
        # Set before anything reads the config so every file (settings, models,
        # credentials, sessions, audit) lives in the host's own directory.
        target = Path(options.config_dir).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        os.environ["TM_CONFIG_DIR"] = str(target)
    settings = load_settings()
    set_language(settings.language)
    mode = options.mode.strip().lower()
    if mode not in ("policy", "host", "yolo"):
        err.print("[red]--mode must be policy, host, or yolo[/red]")
        raise typer.Exit(code=2)
    if options.approval_style not in ("rpc", "acp"):
        raise typer.Exit(code=2)
    cwd = Path(options.cwd).expanduser().resolve() if options.cwd else Path.cwd()
    if not cwd.is_dir():
        err.print(f"[red]--cwd is not a directory: {cwd}[/red]")
        raise typer.Exit(code=2)

    registry = _make_registry(settings, output=err)
    provider_id = options.provider if options.provider is not None else settings.provider
    model_pattern = options.model if options.model is not None else settings.model
    startup_warnings: list[str] = []
    default_selection: Optional[tuple[Any, Any]] = None
    try:
        default_selection = registry.resolve(model_pattern, provider_id)
    except RegistryError as exc:
        # Do not exit: a host may pick a model itself (``session/create {"model"}``).
        startup_warnings.append(str(exc))
        err.print(f"[yellow]{exc}[/yellow]")

    base_tools = _select_tools(
        options.no_tools,
        options.read_only,
        no_python=options.no_python,
        read_dedupe=settings.read_dedupe,
    )
    telemetry = _make_telemetry(options.telemetry or settings.telemetry)
    store = _make_store(options.durable or settings.durable)
    journal = Journal(config_dir() / "changes" / "changes.jsonl")
    manager = (
        SessionManager(config_dir() / "sessions")
        if base_tools and not options.no_session
        else None
    )
    cache_retention = os.environ.get("TM_CACHE_RETENTION", settings.cache_retention)
    trust_cache: Dict[Path, bool] = {}

    def credential_warning(provider: Any) -> Optional[str]:
        preset = next((item for item in registry.presets() if item.id == provider.id), None)
        if preset is None:
            return None
        if (
            preset.api == "openai-completions"
            and not registry.provider_key_present(provider.id)
            and not is_localhost_url(preset.base_url)
        ):
            hint = preset.api_key_env[0] if preset.api_key_env else "the provider's key"
            return (
                f"provider '{provider.id}' has no API key; store one with "
                f"`tm --login {provider.id}` or set {hint}"
            )
        return None

    def selection_for(
        reference: Optional[str],
    ) -> tuple[Any, Any, list[str]]:
        try:
            if reference:
                chosen_provider, chosen_model = registry.resolve(reference, None)
            elif default_selection is not None:
                chosen_provider, chosen_model = default_selection
            else:
                raise RpcError(
                    NOT_CONFIGURED,
                    "no model is configured; pass \"model\" to session/create "
                    "or run `tm --login <provider>`",
                    {"providers": [preset.id for preset in registry.presets()]},
                )
        except RegistryError as exc:
            raise RpcError(NOT_CONFIGURED, str(exc)) from exc
        warning = credential_warning(chosen_provider)
        return chosen_provider, chosen_model, ([warning] if warning else [])

    def trusted_for(path: Path) -> bool:
        key = path.resolve()
        if key not in trust_cache:
            trust_cache[key] = _resolve_trust(
                settings,
                options.approve,
                options.no_approve,
                prompt=False,
                cwd=key,
            )
        return trust_cache[key]

    def make_approver(
        session_id: Optional[str], wire: Wire
    ) -> Approver:
        if mode == "host":
            if options.approval_style == "acp":
                from tm.rpc.acp import AcpApprover

                return AcpApprover(
                    wire,
                    session_id=session_id,
                    timeout_ms=options.approval_timeout,
                )
            return RpcApprover(
                wire.send, timeout_ms=options.approval_timeout, session_id=session_id
            )
        if mode == "yolo":
            return AutoAllowApprover(source="host")
        return AutoDenyApprover()

    def builder(
        session_options: SessionOptions,
        session: Optional[Session],
        session_id: str,
        wire: Wire,
    ) -> BuiltSession:
        access = session_options.access
        tools = list(base_tools)
        if access == "read-only":
            tools = [tool for tool in tools if tool.name in READ_TOOLS]
        if session_options.tools is not None:
            available = {tool.name for tool in tools}
            unknown = sorted(set(session_options.tools) - available)
            if unknown:
                raise ValueError(
                    f"unknown tool(s) for this session: {', '.join(unknown)}"
                )
            wanted = set(session_options.tools)
            tools = [tool for tool in tools if tool.name in wanted]
        trusted = trusted_for(session_options.cwd)
        resources = (
            Resources()
            if options.no_extensions
            else _load_resources(trusted, cwd=session_options.cwd)
        )
        chosen_provider, chosen_model, warnings = selection_for(session_options.model)
        approver = make_approver(session_id, wire)
        agent = _build_agent(
            chosen_provider,
            chosen_model,
            settings,
            tools,
            approver,
            session,
            resources,
            no_context_files=options.no_context_files,
            telemetry=telemetry,
            store=store,
            trusted=trusted,
            cache_retention=cache_retention,
            journal=journal,
            dry_run=options.dry_run or access == "dry-run",
            max_turns=options.max_turns,
            console_ui=False,
            cwd=session_options.cwd,
            instructions=session_options.instructions,
        )
        _restore_session_model(agent, session, registry, settings)
        return BuiltSession(
            agent=agent, approver=approver, journal=journal, warnings=warnings
        )

    initial_access = "read-only" if options.read_only else "normal"
    if options.dry_run:
        initial_access = "dry-run"

    def credentials_guard() -> None:
        if not options.allow_host_credentials:
            raise RpcError(
                UNSUPPORTED,
                "host credential injection is disabled "
                "(start with --allow-host-credentials)",
            )

    async def credentials_set(params: Dict[str, Any]) -> Dict[str, Any]:
        credentials_guard()
        provider_id = require_str(params, "provider")
        key = optional_str(params, "apiKey")
        key_file = optional_str(params, "keyFile")
        if key is None and key_file is None:
            raise RpcError(INVALID_PARAMS, "apiKey or keyFile is required")
        if key is None:
            path = Path(str(key_file)).expanduser()
            try:
                key = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise RpcError(INVALID_PARAMS, f"cannot read keyFile: {exc}") from exc
            if not key:
                raise RpcError(INVALID_PARAMS, "the API key in keyFile is empty")
        key = sanitize_api_key(key)
        if not key:
            raise RpcError(INVALID_PARAMS, "the API key is empty")
        persist = optional_bool(params, "persist")
        try:
            retired = registry.set_api_key(provider_id, key)
        except RegistryError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from exc
        if retired is not None:
            with contextlib.suppress(Exception):
                await retired.aclose()
        if persist:
            save_credential(provider_id, key)
        rebound = host.rebind_provider(provider_id, registry.provider(provider_id))
        return {
            "provider": provider_id,
            "configured": True,
            "persisted": persist,
            "masked": _mask_key(key),
            "sessions": rebound,
        }

    async def credentials_clear(params: Dict[str, Any]) -> Dict[str, Any]:
        credentials_guard()
        provider_id = require_str(params, "provider")
        persist = optional_bool(params, "persist")
        try:
            retired = registry.clear_api_key(provider_id)
        except RegistryError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from exc
        if retired is not None:
            with contextlib.suppress(Exception):
                await retired.aclose()
        if persist:
            delete_credential(provider_id)
        preset = next((item for item in registry.presets() if item.id == provider_id), None)
        still_configured = bool(preset is not None and registry.has_credentials(preset))
        if still_configured:
            host.rebind_provider(provider_id, registry.provider(provider_id))
        return {
            "provider": provider_id,
            "configured": still_configured,
            "persisted": persist,
        }

    def model_switcher(runtime: SessionRuntime, reference: str) -> Dict[str, Any]:
        chosen_provider, chosen_model, warnings = selection_for(reference)
        runtime.agent.provider = chosen_provider
        runtime.agent.model = chosen_model
        apply_compaction_settings(settings, runtime.agent)
        if runtime.session is not None:
            runtime.session.set_model_selection(chosen_provider.id, chosen_model.id)
        return {
            "model": {"provider": chosen_provider.id, "id": chosen_model.id},
            "warnings": warnings,
        }

    async def providers_list(params: Dict[str, Any]) -> Dict[str, Any]:
        entries = []
        for preset in registry.presets():
            entries.append(
                {
                    "id": preset.id,
                    "name": preset.name,
                    "api": preset.api,
                    "defaultModel": preset.default_model,
                    "modelCount": len(preset.models),
                    "hasCredentials": registry.has_credentials(preset)
                    or is_localhost_url(preset.base_url),
                }
            )
        return {
            "providers": entries,
            "current": (
                {
                    "provider": default_selection[0].id,
                    "id": default_selection[1].id,
                }
                if default_selection is not None
                else None
            ),
        }

    async def models_list(params: Dict[str, Any]) -> Dict[str, Any]:
        wanted = params.get("provider")
        entries = []
        for preset in registry.presets():
            if isinstance(wanted, str) and wanted and preset.id != wanted:
                continue
            for entry in preset.models:
                entries.append(
                    {
                        "provider": preset.id,
                        "id": entry.id,
                        "contextWindow": entry.context_window,
                        "maxTokens": entry.max_tokens,
                        "reasoning": entry.reasoning,
                    }
                )
        return {"models": entries}

    extra_methods: Dict[str, HandlerFn] = {
        "providers/list": providers_list,
        "models/list": models_list,
    }
    if options.allow_host_credentials:
        extra_methods["credentials/set"] = credentials_set
        extra_methods["credentials/clear"] = credentials_clear
    host = ServerHost(
        builder,
        manager=manager,
        mode=mode,
        max_sessions=options.max_sessions,
        initial=(
            SessionOptions(cwd=cwd, access=initial_access)
            if default_selection is not None
            else None
        ),
        extra_methods=extra_methods,
        model_switcher=model_switcher,
        warnings=startup_warnings,
    )
    return host, registry


@app.command()
def serve(
    mode: str = typer.Option(
        "policy",
        "--mode",
        help="Approval mode: policy (deny asks), host (ask the host), yolo (allow).",
    ),
    cwd: Optional[str] = typer.Option(None, "--cwd", help="Working directory for the session."),
    stdio: bool = typer.Option(
        True, "--stdio", help="Serve over stdin/stdout (the only transport in this release)."
    ),
    provider: Optional[str] = typer.Option(None, "--provider", help="Provider id."),
    model: Optional[str] = typer.Option(None, "--model", help="Model id or pattern."),
    read_only: bool = typer.Option(False, "--read-only", help="Only read-only tools."),
    no_python: bool = typer.Option(False, "--no-python", help="Disable the python tool."),
    no_tools: bool = typer.Option(False, "--no-tools", help="No tools: plain chat."),
    no_session: bool = typer.Option(False, "--no-session", help="Do not persist sessions."),
    no_context_files: bool = typer.Option(
        False, "--no-context-files", help="Do not load AGENTS.md/CLAUDE.md."
    ),
    no_extensions: bool = typer.Option(
        False, "--no-extensions", help="Do not load .aiagent/extensions."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would change without mutating the machine."
    ),
    approve: bool = typer.Option(
        False, "--approve", "-a", help="Trust project-local files for this run."
    ),
    no_approve: bool = typer.Option(
        False, "--no-approve", "-na", help="Ignore project-local files for this run."
    ),
    approval_timeout: int = typer.Option(
        120000,
        "--approval-timeout",
        help="Milliseconds to wait for a host approval before denying.",
    ),
    config_dir: Optional[str] = typer.Option(
        None,
        "--config-dir",
        help="Config directory for this instance (settings, models, sessions, audit).",
    ),
    max_turns: Optional[int] = typer.Option(
        None, "--max-turns", help="Max agent turns per prompt (default from settings)."
    ),
    max_sessions: int = typer.Option(
        4, "--max-sessions", help="How many sessions this process may host."
    ),
    allow_host_credentials: bool = typer.Option(
        False,
        "--allow-host-credentials",
        help="Allow the host to inject provider API keys at runtime (credentials/set).",
    ),
    telemetry_flag: bool = typer.Option(
        False, "--telemetry", help="Write redacted spans to <config>/telemetry.jsonl."
    ),
    durable_flag: bool = typer.Option(
        False, "--durable", help="Persist a durable restart point per run."
    ),
) -> None:
    if not stdio:
        err.print("[red]only --stdio is supported[/red]")
        raise typer.Exit(code=2)
    options = EmbeddingOptions(
        mode=mode,
        cwd=cwd,
        provider=provider,
        model=model,
        read_only=read_only,
        no_python=no_python,
        no_tools=no_tools,
        no_session=no_session,
        no_context_files=no_context_files,
        no_extensions=no_extensions,
        dry_run=dry_run,
        approve=approve,
        no_approve=no_approve,
        approval_timeout=approval_timeout,
        max_turns=max_turns,
        telemetry=telemetry_flag,
        durable=durable_flag,
        max_sessions=max_sessions,
        allow_host_credentials=allow_host_credentials,
        config_dir=config_dir,
    )
    host, registry = build_host(options)
    err.print(
        f"tm serve: mode={options.mode} maxSessions={options.max_sessions} "
        f"cwd={Path(options.cwd).expanduser().resolve() if options.cwd else Path.cwd()}",
        markup=False,
        highlight=False,
    )

    async def run() -> None:
        server = RpcServer(host)
        try:
            await server.run()
        finally:
            await registry.aclose()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


__all__ = ["EmbeddingOptions", "app", "build_host", "err"]
