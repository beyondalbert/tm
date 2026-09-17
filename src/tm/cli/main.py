"""TM command line interface."""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import typer
from rich.console import Console

from tm import __version__
from tm.ai.providers.base import Provider, StreamOptions
from tm.ai.registry import Registry, RegistryError
from tm.ai.types import (
    AssistantMessage,
    Context,
    ErrorEvent,
    Message,
    Model,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    UserMessage,
)
from tm.ai.user_models import UserModelError, load_presets
from tm.cli.commands import CommandContext, ExitSignal, SlashCommands, _format_time
from tm.cli.console_ui import ConsoleAgentUI
from tm.config import (
    Settings,
    config_dir,
    load_credentials,
    load_settings,
    sanitize_api_key,
    save_credential,
)
from tm.context import build_context_section, load_context_files
from tm.core.agent import Agent
from tm.core.events import AgentEvent
from tm.core.session import Session, SessionInfo, SessionManager
from tm.core.store import Store
from tm.core.system_prompt import build_system_prompt
from tm.extensions import ExtensionAPI, extension_roots, load_extensions
from tm.permissions import (
    READ_TOOLS,
    AuditLog,
    AutoAllowApprover,
    AutoDenyApprover,
    ConsoleApprover,
    PermissionChecker,
    Policy,
    build_permission_hook,
)
from tm.prompts import PromptTemplate, load_prompt_templates, prompt_roots
from tm.skills import Skill, build_skills_section, load_skills, skill_roots
from tm.telemetry import FileTelemetry, NoopTelemetry, Telemetry
from tm.tools import build_default_tools
from tm.trust import TrustManager, project_resources

app = typer.Typer(
    add_completion=False,
    help="The Machine: a local, permission-gated AI agent.",
)
console = Console()


@dataclass
class Resources:
    skills: list[Skill] = field(default_factory=list)
    templates: dict[str, PromptTemplate] = field(default_factory=dict)
    extensions: ExtensionAPI = field(default_factory=ExtensionAPI)


# ---------------------------------------------------------------------------
# Plain chat (no tools)
# ---------------------------------------------------------------------------
async def _display(
    provider: Provider,
    model: Model,
    context: Context,
    options: StreamOptions,
) -> AssistantMessage:
    stream = provider.stream(model, context, options)
    text_started = False
    thinking_started = False
    async for event in stream:
        if isinstance(event, ThinkingDeltaEvent):
            if not thinking_started:
                console.print("[dim]thinking[/dim]")
                thinking_started = True
            console.print(event.delta, end="", style="dim", markup=False, highlight=False)
        elif isinstance(event, TextDeltaEvent):
            if thinking_started and not text_started:
                console.print()
            console.print(event.delta, end="", markup=False, highlight=False)
            text_started = True
        elif isinstance(event, ErrorEvent):
            console.print(f"\n[red]error: {event.error}[/red]")

    final = await stream.result()
    if text_started or thinking_started:
        console.print()
    return final


async def _chat_once(
    provider: Provider, model: Model, text: str, system_prompt: str | None, settings: Settings
) -> None:
    context = Context(system_prompt=system_prompt, messages=[UserMessage(content=text)])
    options = StreamOptions(temperature=settings.temperature, max_tokens=settings.max_tokens)
    await _display(provider, model, context, options)


async def _chat_repl(
    provider: Provider, model: Model, system_prompt: str | None, settings: Settings
) -> None:
    messages: list[Message] = []
    console.print(f"[bold]TM[/bold] {__version__} | {model.provider}/{model.id} | /exit to quit")
    while True:
        try:
            line = await asyncio.to_thread(input, "you> ")
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        line = line.strip()
        if not line:
            continue
        if line in ("/exit", "/quit"):
            break
        messages.append(UserMessage(content=line))
        context = Context(system_prompt=system_prompt, messages=messages)
        options = StreamOptions(
            temperature=settings.temperature, max_tokens=settings.max_tokens
        )
        messages.append(await _display(provider, model, context, options))


# ---------------------------------------------------------------------------
# Agent mode (tools + permissions)
# ---------------------------------------------------------------------------
async def _json_listener(event: AgentEvent) -> None:
    print(event.model_dump_json(), flush=True)


def _select_tools(no_tools: bool, read_only: bool) -> list:
    if no_tools:
        return []
    tools = build_default_tools()
    if read_only:
        return [tool for tool in tools if tool.name in READ_TOOLS]
    return tools


def _make_approver(yolo: bool):
    if yolo:
        return AutoAllowApprover()
    if sys.stdin.isatty():
        return ConsoleApprover(console)
    return AutoDenyApprover()


def _make_registry() -> Registry:
    try:
        presets = load_presets(config_dir())
    except UserModelError as exc:
        console.print(f"[red]models.toml: {exc}[/red]")
        presets = None
    return Registry(presets=presets, api_keys=load_credentials())


def _make_telemetry(enabled: bool) -> Telemetry:
    if not enabled:
        return NoopTelemetry()
    return FileTelemetry(config_dir() / "telemetry.jsonl")


def _mask_key(key: str) -> str:
    """Masked preview of a key so the user can confirm what was stored."""
    if not key:
        return "<empty>"
    if len(key) <= 10:
        return "*" * len(key)
    return f"{key[:6]}...{key[-4:]} ({len(key)} chars)"


def _make_store(enabled: bool) -> Store | None:
    if not enabled:
        return None
    return Store.open(config_dir() / "state.jsonl")


def _load_resources(trusted: bool) -> Resources:
    cwd = Path.cwd()
    return Resources(
        skills=load_skills(skill_roots(cwd, config_dir(), include_project=trusted)),
        templates=load_prompt_templates(prompt_roots(cwd, config_dir(), include_project=trusted)),
        extensions=load_extensions(extension_roots(cwd, config_dir(), include_project=trusted)),
    )


def _resolve_trust(settings: Settings, approve: bool, no_approve: bool) -> bool:
    """Decide whether project-local resources may load.

    Order: CLI override, then a saved decision, then ``default_project_trust``.
    ``ask`` with no terminal (non-interactive modes) declines, matching pi.
    """
    cwd = Path.cwd()
    resources = project_resources(cwd)
    if not resources:
        return True
    if approve:
        return True
    if no_approve:
        return False
    manager = TrustManager(config_dir() / "trust.json")
    saved = manager.decision(cwd)
    if saved is not None:
        return saved
    default = (settings.default_project_trust or "ask").lower()
    if default == "always":
        return True
    if default == "never":
        return False
    if not sys.stdin.isatty():
        return False
    console.print(
        f"[yellow]{cwd} has project-local resources that can run code or change permissions:[/yellow]"
    )
    for path in resources:
        console.print(f"  [dim]{path}[/dim]")
    answer = input("Load them? [y]es / [n]o / [a]lways: ").strip().lower()
    if answer in ("a", "always"):
        manager.save(cwd, True)
        return True
    return answer in ("y", "yes")


def _system_prompt(settings: Settings, resources: Resources, no_context_files: bool) -> str:
    cwd = Path.cwd()
    extra = settings.system_prompt
    if not no_context_files:
        section = build_context_section(load_context_files(cwd, config_dir()))
        if section:
            extra = f"{extra}\n\n{section}" if extra else section
    skills_section = build_skills_section(resources.skills)
    if skills_section:
        extra = f"{extra}\n\n{skills_section}" if extra else skills_section
    return build_system_prompt(cwd, extra)


def _build_agent(
    provider: Provider,
    model: Model,
    settings: Settings,
    tools: list,
    approver,
    session: Session | None,
    resources: Resources,
    *,
    no_context_files: bool = False,
    no_auto_compact: bool = False,
    json_output: bool = False,
    telemetry: Telemetry | None = None,
    store: Store | None = None,
    trusted: bool = True,
    cache_retention: str | None = None,
) -> Agent:
    cwd = Path.cwd()
    policy = Policy.load(cwd=cwd, config_dir=config_dir(), include_project=trusted)
    checker = PermissionChecker(
        policy,
        approver=approver,
        audit=AuditLog(config_dir() / "audit.jsonl"),
    )
    all_tools = [*tools, *resources.extensions.tools]
    agent = Agent(
        model,
        provider=provider,
        tools=all_tools,
        system_prompt=_system_prompt(settings, resources, no_context_files),
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
        cwd=cwd,
        session=session,
        auto_compact=settings.auto_compact and not no_auto_compact,
        compact_threshold=settings.compact_threshold,
        compact_keep_recent=settings.compact_keep_recent,
        telemetry=telemetry,
        store=store,
        cache_retention=cache_retention,
    )
    agent.before_tool_call = build_permission_hook(checker, cwd)
    for listener in resources.extensions.listeners:
        agent.subscribe(listener)
    agent.subscribe(_json_listener if json_output else ConsoleAgentUI(console).handle)
    return agent


def _command_context(
    agent: Agent,
    registry: Registry,
    session: Session | None,
    session_manager: SessionManager | None,
    resources: Resources,
    emit,
    picker=None,
) -> SlashCommands:
    return SlashCommands(
        CommandContext(
            agent=agent,
            registry=registry,
            cwd=Path.cwd(),
            emit=emit,
            session=session,
            session_manager=session_manager,
            skills=resources.skills,
            templates=resources.templates,
            extensions=resources.extensions,
            picker=picker,
            trust_manager=TrustManager(config_dir() / "trust.json"),
        )
    )


def _session_choices(manager: SessionManager, all_sessions: bool) -> list[SessionInfo]:
    infos = manager.list()
    if all_sessions:
        return infos
    local = [info for info in infos if info.cwd == str(Path.cwd())]
    return local or infos


def _pick_session_console(
    infos: list[SessionInfo], output: Console
) -> SessionInfo | None:
    for index, info in enumerate(infos, start=1):
        label = info.name or info.id
        output.print(
            f"{index}. {label} | {info.message_count} msgs | "
            f"{_format_time(info.updated)} | {info.preview}"
        )
    try:
        choice = input("select session number or id (blank to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not choice:
        return None
    if choice.isdigit():
        index = int(choice)
        if 1 <= index <= len(infos):
            return infos[index - 1]
    for info in infos:
        if info.id.startswith(choice):
            return info
    for info in infos:
        if info.name == choice:
            return info
    return None


async def _console_picker(infos: list[SessionInfo]) -> SessionInfo | None:
    return await asyncio.to_thread(_pick_session_console, infos, console)


def _startup_banner(model: Model, resources: Resources, no_context_files: bool) -> str:
    cwd = Path.cwd()
    lines = [
        f"TM v{__version__}",
        "escape to interrupt  ·  / for commands  ·  ! to run a shell command  ·  ctrl+q to quit",
        "Ask TM to explain its features, or type /help.",
    ]
    if not no_context_files:
        files = load_context_files(cwd, config_dir())
        if files:
            lines.append("")
            lines.append("[Context]")
            lines.extend(f"  {path}" for path, _ in files)
    if resources.skills:
        lines.append("")
        lines.append("[Skills]")
        lines.extend(f"  {skill.name}" for skill in resources.skills)
    if resources.templates:
        lines.append("")
        lines.append("[Prompts]")
        lines.extend(f"  /{name}" for name in sorted(resources.templates))
    if resources.extensions.sources:
        lines.append("")
        lines.append("[Extensions]")
        lines.extend(f"  {path}" for path in resources.extensions.sources)
    return "\n".join(lines)


def _textual_available() -> bool:
    try:
        import textual  # noqa: F401
    except ImportError:
        return False
    return True


def _use_tui(
    *,
    tui: bool,
    no_tui: bool,
    tools: list,
    text: str | None,
    json_output: bool,
) -> bool:
    if not tools or json_output or no_tui:
        return False
    if tui:
        return True
    return text is None and sys.stdout.isatty() and _textual_available()


def _run_tui(
    provider: Provider,
    model: Model,
    settings: Settings,
    tools: list,
    session: Session | None,
    session_manager: SessionManager | None,
    resources: Resources,
    no_context_files: bool,
    no_auto_compact: bool,
    resume_on_start: bool,
    telemetry: Telemetry,
    store: Store | None,
    mouse: bool,
    trusted: bool,
    cache_retention: str | None,
) -> None:
    from tm.tui import DeferredApprover, TextualApprover, TMPromptApp

    deferred = DeferredApprover()
    agent = _build_agent(
        provider,
        model,
        settings,
        tools,
        deferred,
        session,
        resources,
        no_context_files=no_context_files,
        no_auto_compact=no_auto_compact,
        telemetry=telemetry,
        store=store,
        trusted=trusted,
        cache_retention=cache_retention,
    )
    prompt_app = TMPromptApp(
        agent, model, banner=_startup_banner(model, resources, no_context_files)
    )
    prompt_app.resume_on_start = resume_on_start
    prompt_app.recover_on_start = bool(agent.pending_recovery())
    commands = _command_context(
        agent,
        _make_registry(),
        session,
        session_manager,
        resources,
        prompt_app.write_line,
        picker=prompt_app.pick_session,
    )
    prompt_app.command_handler = commands.handle
    deferred.set(TextualApprover(prompt_app))
    prompt_app.run(mouse=mouse)


async def _agent_repl(
    agent: Agent, model: Model, registry: Registry, session_manager: SessionManager | None,
    resources: Resources, session: Session | None,
) -> None:
    commands = _command_context(
        agent,
        registry,
        session,
        session_manager,
        resources,
        console.print,
        picker=_console_picker,
    )
    console.print(
        f"[bold]TM[/bold] {__version__} | {model.provider}/{model.id} | /help | /exit"
    )
    while True:
        try:
            line = await asyncio.to_thread(input, "you> ")
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        line = line.strip()
        if not line:
            continue
        if line.startswith("/"):
            try:
                forwarded = await commands.handle(line[1:])
            except ExitSignal:
                break
            if forwarded is None:
                continue
            line = forwarded
        try:
            await agent.prompt(line)
        except KeyboardInterrupt:
            agent.abort()
            console.print("[yellow]aborted[/yellow]")
        console.print()


def _resolve_session(
    tools: list,
    *,
    continue_session: bool,
    new_session: bool,
    resume: bool,
    session_path: str | None,
    no_session: bool,
    tui: bool,
    all_sessions: bool,
) -> tuple[Session | None, SessionManager | None, bool]:
    if not tools or no_session:
        return None, None, False
    manager = SessionManager(config_dir() / "sessions")
    if session_path:
        return manager.open(Path(session_path)), manager, False
    if resume:
        choices = _session_choices(manager, all_sessions)
        if not choices:
            console.print("[dim]no saved sessions[/dim]")
        elif tui:
            return None, manager, True
        else:
            chosen = _pick_session_console(choices, console)
            if chosen is not None:
                return manager.open(chosen.path), manager, False
        return manager.create(cwd=Path.cwd()), manager, False
    if not new_session:
        # Default: continue the most recent session for this directory.
        existing = manager.continue_recent(Path.cwd())
        if existing is not None:
            if continue_session:
                console.print(f"[dim]resuming session {existing.id}[/dim]")
            return existing, manager, False
    return manager.create(cwd=Path.cwd()), manager, False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
@app.command()
def main(
    prompt: list[str] | None = typer.Argument(None, help="Prompt. Omit for a REPL."),
    provider: str | None = typer.Option(None, "--provider", "-p", help="Provider id."),
    model: str | None = typer.Option(None, "--model", "-m", help="Model id or pattern."),
    no_tools: bool = typer.Option(False, "--no-tools", "-nt", help="Disable tools."),
    read_only: bool = typer.Option(False, "--read-only", help="Only read-only tools."),
    yolo: bool = typer.Option(False, "--yolo", help="Auto-approve all actions."),
    tui: bool = typer.Option(False, "--tui", help="Force the Textual TUI."),
    no_tui: bool = typer.Option(
        False, "--no-tui", help="Use the plain console REPL instead of the TUI."
    ),
    print_mode: bool = typer.Option(False, "--print", help="One-shot; read stdin if no prompt."),
    json_output: bool = typer.Option(False, "--json", help="Emit agent events as JSON lines."),
    continue_session: bool = typer.Option(
        False, "--continue", "-c", help="Continue the most recent session here (the default)."
    ),
    new_session: bool = typer.Option(
        False,
        "--new-session",
        "--new",
        help="Start a fresh session instead of continuing the most recent one.",
    ),
    resume: bool = typer.Option(
        False, "--resume", "-r", help="Pick a saved session to resume."
    ),
    all_sessions: bool = typer.Option(
        False, "--all-sessions", help="Include sessions from other directories."
    ),
    session_path: str | None = typer.Option(None, "--session", help="Open a session file."),
    no_session: bool = typer.Option(False, "--no-session", help="Do not persist this session."),
    no_context_files: bool = typer.Option(
        False, "--no-context-files", "-nc", help="Do not load AGENTS.md/CLAUDE.md."
    ),
    no_extensions: bool = typer.Option(
        False, "--no-extensions", help="Do not load .aiagent/extensions."
    ),
    no_auto_compact: bool = typer.Option(
        False, "--no-auto-compact", help="Disable automatic context compaction."
    ),
    telemetry_flag: bool = typer.Option(
        False, "--telemetry", help="Write redacted spans to <config>/telemetry.jsonl."
    ),
    durable_flag: bool = typer.Option(
        False,
        "--durable",
        help="Persist a durable restart point per run to <config>/state.jsonl.",
    ),
    no_mouse: bool = typer.Option(
        False,
        "--no-mouse",
        help="Let the terminal handle mouse selection/copy (disables in-app mouse).",
    ),
    approve: bool = typer.Option(
        False, "--approve", "-a", help="Trust project-local files for this run."
    ),
    no_approve: bool = typer.Option(
        False, "--no-approve", "-na", help="Ignore project-local files for this run."
    ),
    login: str | None = typer.Option(
        None, "--login", help="Store an API key for a provider and exit."
    ),
    list_models: bool = typer.Option(False, "--list-models", help="List known models."),
    version: bool = typer.Option(False, "--version", "-v", help="Show version."),
) -> None:
    if version:
        console.print(__version__)
        raise typer.Exit()

    settings = load_settings()
    registry = _make_registry()

    if login is not None:
        known = {preset.id for preset in registry.presets()}
        if login not in known:
            console.print(f"[red]Unknown provider: {login}[/red]")
            console.print(f"Known providers: {', '.join(sorted(known))}")
            raise typer.Exit(code=1)
        key = input(f"Paste API key for {login}: ").strip()
        key = sanitize_api_key(key)
        if not key:
            console.print("[red]No key entered[/red]")
            raise typer.Exit(code=1)
        saved = save_credential(login, key)
        console.print(f"Saved {login} credential to [bold]{saved}[/bold]")
        console.print(f"[dim]key: {_mask_key(key)}[/dim]")
        raise typer.Exit()

    if list_models:
        for preset in registry.presets():
            marker = "*" if registry.has_credentials(preset) else "-"
            for entry in preset.models:
                console.print(f"{marker} {entry.provider}/{entry.id}")
        raise typer.Exit()

    provider_id: str | None
    model_pattern: str | None
    if provider is None and model is None:
        provider_id, model_pattern = settings.provider, settings.model
    else:
        provider_id, model_pattern = provider, model

    try:
        selected_provider, selected_model = registry.resolve(model_pattern, provider_id)
    except RegistryError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    tools = _select_tools(no_tools, read_only)
    trusted = _resolve_trust(settings, approve, no_approve)
    if not trusted:
        console.print(
            "[dim]project-local extensions/skills/prompts/policy ignored (untrusted); "
            "use --approve or /trust[/dim]"
        )
    resources = Resources() if no_extensions else _load_resources(trusted)
    text = " ".join(prompt) if prompt else None
    if text is None and (print_mode or json_output) and not sys.stdin.isatty():
        piped = sys.stdin.read().strip()
        text = piped or None
    if text is None and json_output:
        console.print("[red]--json requires a prompt or piped stdin[/red]")
        raise typer.Exit(code=1)

    use_tui = _use_tui(
        tui=tui, no_tui=no_tui, tools=tools, text=text, json_output=json_output
    )
    if tui and not _textual_available():
        console.print("[red]TUI requested but 'textual' is not installed. Install with: pip install 'the-machine[tui]'[/red]")
        raise typer.Exit(code=1)

    session, session_manager, resume_on_start = _resolve_session(
        tools,
        continue_session=continue_session,
        new_session=new_session,
        resume=resume,
        session_path=session_path,
        no_session=no_session,
        tui=use_tui,
        all_sessions=all_sessions,
    )

    telemetry = _make_telemetry(telemetry_flag or settings.telemetry)
    store = _make_store(durable_flag or settings.durable)
    mouse = settings.mouse and not no_mouse
    cache_retention = os.environ.get("TM_CACHE_RETENTION", settings.cache_retention)

    async def run() -> None:
        try:
            if tools:
                agent = _build_agent(
                    selected_provider,
                    selected_model,
                    settings,
                    tools,
                    _make_approver(yolo),
                    session,
                    resources,
                    no_context_files=no_context_files,
                    no_auto_compact=no_auto_compact,
                    json_output=json_output,
                    telemetry=telemetry,
                    store=store,
                    trusted=trusted,
                    cache_retention=cache_retention,
                )
                if store is not None and await agent.recover():
                    console.print("[dim]recovered an interrupted operation[/dim]")
                if text:
                    await agent.prompt(text)
                    if not json_output:
                        console.print()
                else:
                    await _agent_repl(
                        agent, selected_model, registry, session_manager, resources, session
                    )
            elif text:
                await _chat_once(
                    selected_provider, selected_model, text, settings.system_prompt, settings
                )
            else:
                await _chat_repl(
                    selected_provider, selected_model, settings.system_prompt, settings
                )
        finally:
            # Close providers inside the same event loop that created their clients.
            await registry.aclose()

    try:
        if use_tui:
            _run_tui(
                selected_provider,
                selected_model,
                settings,
                tools,
                session,
                session_manager,
                resources,
                no_context_files,
                no_auto_compact,
                resume_on_start,
                telemetry,
                store,
                mouse,
                trusted,
                cache_retention,
            )
        else:
            asyncio.run(run())
    except (KeyboardInterrupt, EOFError):
        console.print()


if __name__ == "__main__":
    app()
