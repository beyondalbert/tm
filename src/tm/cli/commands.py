"""Interactive slash commands for the agent REPL and TUI."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from tm.ai.registry import Registry, RegistryError
from tm.ai.types import AssistantMessage, Message, ToolResultMessage, UserMessage
from tm.core.agent import Agent
from tm.core.session import Session, SessionInfo, SessionManager
from tm.extensions import ExtensionAPI
from tm.host import detect_host
from tm.prompts import PromptTemplate, expand_template
from tm.safety import Journal, apply_undo
from tm.skills import Skill, find_skill
from tm.trust import TrustManager

if TYPE_CHECKING:
    from tm.permissions.approval import SessionApprover

Emit = Callable[[str], None]
SessionPicker = Callable[[list[SessionInfo]], Awaitable[SessionInfo | None]]


def _preview(message: Message, limit: int = 60) -> str:
    if isinstance(message, UserMessage):
        text = message.content if isinstance(message.content, str) else "[multimodal]"
    elif isinstance(message, AssistantMessage):
        text = message.text() or ("[tool calls]" if message.tool_calls else "[empty]")
    elif isinstance(message, ToolResultMessage):
        text = f"[{message.tool_name}] {message.text()}"
    else:
        text = ""
    text = " ".join(text.split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _format_time(milliseconds: int) -> str:
    try:
        return datetime.fromtimestamp(milliseconds / 1000).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return "?"


class ExitSignal(Exception):
    """Raised by /exit and /quit to leave the loop."""


@dataclass
class CommandContext:
    agent: Agent
    registry: Registry
    cwd: Path
    emit: Emit
    session: Session | None = None
    session_manager: SessionManager | None = None
    skills: list[Skill] | None = None
    templates: dict[str, PromptTemplate] | None = None
    extensions: ExtensionAPI | None = None
    picker: SessionPicker | None = None
    trust_manager: TrustManager | None = None
    journal: Journal | None = None
    approver: SessionApprover | None = None


COMMAND_SPECS: list[tuple[str, str]] = [
    ("help", "show this help"),
    ("model", "list models or switch model"),
    ("new", "start a new session"),
    ("session", "show current session info"),
    ("resume", "resume a saved session"),
    ("tree", "list conversation points or branch"),
    ("fork", "fork the session into a new file"),
    ("compact", "summarize older context"),
    ("recover", "reconcile interrupted durable operations"),
    ("undo", "roll back the last change(s)"),
    ("auto", "toggle auto-approve for this session"),
    ("trust", "trust or untrust this project"),
    ("skills", "list available skills"),
    ("prompts", "list prompt templates"),
    ("exit", "quit"),
]

HELP = """commands:
  /help                 show this help
  /model [pattern]      list models or switch model
  /new                  start a new session
  /session              show current session info
  /resume [n|id]        resume a saved session (picker when no argument)
  /tree [n]             list conversation points or branch from point n
  /fork [n]             fork the session (at point n) into a new file
  /compact [note]       summarize older context
  /recover              reconcile interrupted durable operations
  /undo [n]             roll back the last n reversible changes
  /auto [on|off]        toggle auto-approve for this session
  /trust [off]          trust (or untrust) this project for future sessions
  /skills               list available skills
  /skill:<name>         load a skill into the conversation
  /prompts              list prompt templates
  /<template> [args]    expand a prompt template
  /exit                 quit"""


class SlashCommands:
    def __init__(self, ctx: CommandContext) -> None:
        self.ctx = ctx

    def _emit(self, message: str) -> None:
        self.ctx.emit(message)

    async def handle(self, text: str) -> str | None:
        """Handle a line without its leading slash.

        Returns a prompt to send to the agent, or None if fully handled.
        """
        parts = text.split(None, 1)
        command = parts[0] if parts else ""
        argument = parts[1].strip() if len(parts) > 1 else ""

        if command in ("exit", "quit"):
            raise ExitSignal
        if command == "help":
            self._emit(HELP)
            return None
        if command == "new":
            await self._new_session()
            return None
        if command == "session":
            self._show_session()
            return None
        if command == "resume":
            await self._resume(argument)
            return None
        if command == "tree":
            self._tree(argument)
            return None
        if command == "fork":
            self._fork(argument)
            return None
        if command == "model":
            self._model(argument)
            return None
        if command == "skills":
            self._list_skills()
            return None
        if command.startswith("skill:"):
            return self._load_skill(command[len("skill:") :])
        if command == "prompts":
            self._list_prompts()
            return None
        if command == "compact":
            await self._compact(argument or None)
            return None
        if command == "recover":
            await self._recover()
            return None
        if command == "undo":
            self._undo(argument)
            return None
        if command == "auto":
            self._auto(argument)
            return None
        if command == "trust":
            self._trust(argument)
            return None

        templates = self.ctx.templates or {}
        if command in templates:
            return expand_template(templates[command].text, argument)

        extensions = self.ctx.extensions
        if extensions is not None and command in extensions.commands:
            result = extensions.commands[command](argument)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, str) and result:
                self._emit(result)
            return None

        self._emit(f"unknown command: /{command} (try /help)")
        return None

    async def _new_session(self) -> None:
        self.ctx.agent.reset()
        if self.ctx.session_manager is not None:
            session = self.ctx.session_manager.create(cwd=self.ctx.cwd)
            self.ctx.session = session
            self.ctx.agent.session = session
            if self.ctx.agent.provider is not None:
                session.set_model_selection(
                    self.ctx.agent.provider.id, self.ctx.agent.model.id
                )
            self._emit(f"new session {session.id}")
        else:
            self._emit("conversation cleared")

    def _apply_session_model(self) -> None:
        """Restore the provider/model the session remembers, if any."""
        session = self.ctx.session
        if session is None:
            return
        selection = session.model_selection()
        if selection is None:
            return
        provider_id, model_id = selection
        try:
            provider, model = self.ctx.registry.resolve(model_id, provider_id)
        except RegistryError:
            return
        self.ctx.agent.provider = provider
        self.ctx.agent.model = model

    def _show_session(self) -> None:
        session = self.ctx.session
        if session is None:
            self._emit("no session (session persistence disabled)")
            return
        count = len(self.ctx.agent.messages)
        self._emit(f"session {session.id} | {count} messages | {session.path}")

    def _session_choices(self) -> list[SessionInfo]:
        manager = self.ctx.session_manager
        if manager is None:
            return []
        infos = manager.list()
        local = [info for info in infos if info.cwd == str(self.ctx.cwd)]
        return local or infos

    def _list_sessions(self, infos: list[SessionInfo]) -> None:
        lines = []
        for index, info in enumerate(infos, start=1):
            label = info.name or info.id
            lines.append(
                f"{index}. {label} | {info.message_count} msgs | "
                f"{_format_time(info.updated)} | {info.preview}"
            )
        lines.append("use /resume <n> or /resume <id>")
        self._emit("\n".join(lines))

    @staticmethod
    def _match_session(infos: list[SessionInfo], argument: str) -> SessionInfo | None:
        argument = argument.strip()
        if argument.isdigit():
            index = int(argument)
            if 1 <= index <= len(infos):
                return infos[index - 1]
        for info in infos:
            if info.id.startswith(argument):
                return info
        for info in infos:
            if info.name and info.name == argument:
                return info
        return None

    async def _resume(self, argument: str) -> None:
        manager = self.ctx.session_manager
        if manager is None:
            self._emit("no session (session persistence disabled)")
            return
        infos = self._session_choices()
        if not infos:
            self._emit("no saved sessions")
            return

        chosen: SessionInfo | None = None
        if argument:
            chosen = self._match_session(infos, argument)
            if chosen is None:
                self._emit(f"no session matching '{argument}'")
                return
        elif self.ctx.picker is not None:
            chosen = await self.ctx.picker(infos)
        else:
            self._list_sessions(infos)
            return

        if chosen is None:
            self._emit("resume cancelled")
            return
        session = manager.open(chosen.path)
        self.ctx.session = session
        self.ctx.agent.resume(session)
        self._apply_session_model()
        self._emit(f"resumed session {session.id} ({len(session.messages())} messages)")

    def _tree(self, argument: str) -> None:
        session = self.ctx.session
        if session is None:
            self._emit("no session (session persistence disabled)")
            return
        entries = session.points()
        if not argument:
            if not entries:
                self._emit("no conversation points yet")
                return
            lines = []
            for index, entry in enumerate(entries, start=1):
                marker = "*" if entry.id == session.leaf_id else " "
                preview = _preview(entry.message) if entry.message is not None else ""
                lines.append(f"{marker} {index}. {preview}")
            lines.append("use /tree <n> to branch, /fork <n> to fork into a new file")
            self._emit("\n".join(lines))
            return
        try:
            index = int(argument)
        except ValueError:
            self._emit("usage: /tree <n>")
            return
        if not 1 <= index <= len(entries):
            self._emit(f"point out of range (1..{len(entries)})")
            return
        session.branch_from(entries[index - 1].id)
        self.ctx.agent.set_messages(session.messages())
        self._emit(f"branched at point {index}; {len(session.messages())} messages active")

    def _fork(self, argument: str) -> None:
        session = self.ctx.session
        manager = self.ctx.session_manager
        if session is None or manager is None:
            self._emit("no session (session persistence disabled)")
            return
        target = session.leaf_id
        if argument:
            entries = session.points()
            try:
                index = int(argument)
            except ValueError:
                self._emit("usage: /fork <n>")
                return
            if not 1 <= index <= len(entries):
                self._emit(f"point out of range (1..{len(entries)})")
                return
            target = entries[index - 1].id
        if target is None:
            self._emit("nothing to fork")
            return
        forked = manager.fork(session, target, cwd=self.ctx.cwd)
        self.ctx.session = forked
        self.ctx.agent.resume(forked)
        self._emit(f"forked to session {forked.id} | {forked.path}")

    def _model(self, argument: str) -> None:
        registry = self.ctx.registry
        if not argument:
            lines = []
            for preset in registry.presets():
                models = ", ".join(model.id for model in preset.models)
                lines.append(f"{preset.id}: {models or '(none)'}")
            self._emit("models (/model <id> to switch):\n" + "\n".join(lines))
            return
        try:
            provider, model = registry.resolve(argument, None)
        except RegistryError as exc:
            self._emit(str(exc))
            return
        self.ctx.agent.provider = provider
        self.ctx.agent.model = model
        if self.ctx.session is not None:
            self.ctx.session.set_model_selection(provider.id, model.id)
        self._emit(f"model set to {model.provider}/{model.id}")

    def _list_skills(self) -> None:
        skills = self.ctx.skills or []
        if not skills:
            self._emit("no skills found")
            return
        lines = [f"{skill.name}: {skill.description}" for skill in skills]
        self._emit("skills:\n" + "\n".join(lines))

    def _load_skill(self, name: str) -> str | None:
        skill = find_skill(self.ctx.skills or [], name)
        if skill is None:
            self._emit(f"no such skill: {name}")
            return None
        return f"Apply the following skill:\n\n{skill.body}"

    def _list_prompts(self) -> None:
        templates = self.ctx.templates or {}
        if not templates:
            self._emit("no prompt templates found")
            return
        self._emit("prompt templates: " + ", ".join(sorted(templates)))

    async def _compact(self, instructions: str | None) -> None:
        changed = await self.ctx.agent.compact(instructions)
        self._emit("compacted" if changed else "nothing to compact")

    async def _recover(self) -> None:
        pending = self.ctx.agent.pending_recovery()
        if not pending:
            self._emit("nothing to recover")
            return
        await self.ctx.agent.recover()
        self._emit(f"recovered {len(pending)} interrupted operation(s)")

    def _undo(self, argument: str) -> None:
        journal = self.ctx.journal
        if journal is None:
            self._emit("no change journal available")
            return
        count = int(argument) if argument.strip().isdigit() else 1
        session = self.ctx.session.id if self.ctx.session is not None else None
        changes = [change for change in journal.last(count, session=session) if change.reversible]
        if not changes:
            self._emit("nothing to undo")
            return
        host = detect_host()
        for change in reversed(changes):
            self._emit(apply_undo(change, host))
            journal.mark(change.id, "reverted")

    def _auto(self, argument: str) -> None:
        approver = self.ctx.approver
        if approver is None:
            self._emit("auto-approve is not available")
            return
        choice = argument.strip().lower()
        if choice in ("on", "true", "yes", "1"):
            approver.auto = True
        elif choice in ("off", "false", "no", "0"):
            approver.auto = False
        else:
            approver.auto = not approver.auto
        self._emit(f"auto-approve {'on' if approver.auto else 'off'}")

    def _trust(self, argument: str) -> None:
        manager = self.ctx.trust_manager
        if manager is None:
            self._emit("trust is not available")
            return
        trusted = argument.strip().lower() not in ("off", "no", "false", "untrust")
        manager.save(self.ctx.cwd, trusted)
        state = "trusted" if trusted else "untrusted"
        self._emit(f"{state} {self.ctx.cwd} (restart to apply)")


__all__ = [
    "COMMAND_SPECS",
    "HELP",
    "CommandContext",
    "Emit",
    "ExitSignal",
    "SessionPicker",
    "SlashCommands",
]
