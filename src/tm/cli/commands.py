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
from tm.clipboard import copy_to_clipboard
from tm.core.agent import Agent
from tm.core.session import Session, SessionInfo, SessionManager
from tm.extensions import ExtensionAPI
from tm.host import detect_host
from tm.i18n import get_language, set_language
from tm.i18n import t as _t
from tm.prompts import PromptTemplate, expand_template
from tm.safety import Journal, apply_undo
from tm.skills import Skill, find_skill
from tm.trust import TrustManager

if TYPE_CHECKING:
    from tm.permissions.approval import SessionApprover

Emit = Callable[[str], None]
SessionPicker = Callable[[list[SessionInfo]], Awaitable[SessionInfo | None]]

_SPEC_NAMES = (
    "help",
    "model",
    "new",
    "session",
    "resume",
    "tree",
    "fork",
    "compact",
    "recover",
    "undo",
    "copy",
    "auto",
    "lang",
    "trust",
    "skills",
    "prompts",
    "exit",
)


def command_specs() -> list[tuple[str, str]]:
    return [(name, _t(f"spec.{name}")) for name in _SPEC_NAMES]


def help_text() -> str:
    return _t("help.text")


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
            self._emit(help_text())
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
        if command == "copy":
            self._copy(argument)
            return None
        if command == "lang":
            self._lang(argument)
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

        self._emit(_t("cmd.unknown", command=command))
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
            self._emit(_t("cmd.new_session", id=session.id))
        else:
            self._emit(_t("cmd.cleared"))

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
            self._emit(_t("cmd.no_session"))
            return
        count = len(self.ctx.agent.messages)
        self._emit(_t("cmd.session_info", id=session.id, count=count, path=session.path))

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
        lines.append(_t("cmd.use_resume"))
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
            self._emit(_t("cmd.no_session"))
            return
        infos = self._session_choices()
        if not infos:
            self._emit(_t("cmd.no_sessions"))
            return

        chosen: SessionInfo | None = None
        if argument:
            chosen = self._match_session(infos, argument)
            if chosen is None:
                self._emit(_t("cmd.no_match", argument=argument))
                return
        elif self.ctx.picker is not None:
            chosen = await self.ctx.picker(infos)
        else:
            self._list_sessions(infos)
            return

        if chosen is None:
            self._emit(_t("cmd.resume_cancelled"))
            return
        session = manager.open(chosen.path)
        self.ctx.session = session
        self.ctx.agent.resume(session)
        self._apply_session_model()
        self._emit(_t("cmd.resumed", id=session.id, count=len(session.messages())))

    def _tree(self, argument: str) -> None:
        session = self.ctx.session
        if session is None:
            self._emit(_t("cmd.no_session"))
            return
        entries = session.points()
        if not argument:
            if not entries:
                self._emit(_t("cmd.no_points"))
                return
            lines = []
            for index, entry in enumerate(entries, start=1):
                marker = "*" if entry.id == session.leaf_id else " "
                preview = _preview(entry.message) if entry.message is not None else ""
                lines.append(f"{marker} {index}. {preview}")
            lines.append(_t("cmd.tree_hint"))
            self._emit("\n".join(lines))
            return
        try:
            index = int(argument)
        except ValueError:
            self._emit(_t("cmd.usage_tree"))
            return
        if not 1 <= index <= len(entries):
            self._emit(_t("cmd.out_of_range", count=len(entries)))
            return
        session.branch_from(entries[index - 1].id)
        self.ctx.agent.set_messages(session.messages())
        self._emit(_t("cmd.branched", index=index, count=len(session.messages())))

    def _fork(self, argument: str) -> None:
        session = self.ctx.session
        manager = self.ctx.session_manager
        if session is None or manager is None:
            self._emit(_t("cmd.no_session"))
            return
        target = session.leaf_id
        if argument:
            entries = session.points()
            try:
                index = int(argument)
            except ValueError:
                self._emit(_t("cmd.usage_fork"))
                return
            if not 1 <= index <= len(entries):
                self._emit(_t("cmd.out_of_range", count=len(entries)))
                return
            target = entries[index - 1].id
        if target is None:
            self._emit(_t("cmd.nothing_to_fork"))
            return
        forked = manager.fork(session, target, cwd=self.ctx.cwd)
        self.ctx.session = forked
        self.ctx.agent.resume(forked)
        self._emit(_t("cmd.forked", id=forked.id, path=forked.path))

    def _model(self, argument: str) -> None:
        registry = self.ctx.registry
        if not argument:
            lines = []
            for preset in registry.presets():
                models = ", ".join(model.id for model in preset.models)
                lines.append(f"{preset.id}: {models or '(none)'}")
            self._emit(_t("cmd.models_header") + "\n" + "\n".join(lines))
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
        self._emit(_t("cmd.model_set", provider=model.provider, id=model.id))

    def _list_skills(self) -> None:
        skills = self.ctx.skills or []
        if not skills:
            self._emit(_t("cmd.no_skills"))
            return
        lines = [f"{skill.name}: {skill.description}" for skill in skills]
        self._emit(_t("cmd.skills_header") + "\n" + "\n".join(lines))

    def _load_skill(self, name: str) -> str | None:
        skill = find_skill(self.ctx.skills or [], name)
        if skill is None:
            self._emit(_t("cmd.no_skill", name=name))
            return None
        return f"Apply the following skill:\n\n{skill.body}"

    def _list_prompts(self) -> None:
        templates = self.ctx.templates or {}
        if not templates:
            self._emit(_t("cmd.no_prompts"))
            return
        self._emit(_t("cmd.prompts_header", names=", ".join(sorted(templates))))

    async def _compact(self, instructions: str | None) -> None:
        changed = await self.ctx.agent.compact(instructions)
        self._emit(_t("cmd.compacted") if changed else _t("cmd.nothing_compact"))

    async def _recover(self) -> None:
        pending = self.ctx.agent.pending_recovery()
        if not pending:
            self._emit(_t("cmd.nothing_recover"))
            return
        await self.ctx.agent.recover()
        self._emit(_t("cmd.recovered", count=len(pending)))

    def _undo(self, argument: str) -> None:
        journal = self.ctx.journal
        if journal is None:
            self._emit(_t("cmd.no_journal"))
            return
        count = int(argument) if argument.strip().isdigit() else 1
        session = self.ctx.session.id if self.ctx.session is not None else None
        changes = [change for change in journal.last(count, session=session) if change.reversible]
        if not changes:
            self._emit(_t("cmd.nothing_undo"))
            return
        host = detect_host()
        for change in reversed(changes):
            self._emit(apply_undo(change, host))
            journal.mark(change.id, "reverted")

    def _auto(self, argument: str) -> None:
        approver = self.ctx.approver
        if approver is None:
            self._emit(_t("cmd.auto_off_unavailable"))
            return
        choice = argument.strip().lower()
        if choice in ("on", "true", "yes", "1"):
            approver.auto = True
        elif choice in ("off", "false", "no", "0"):
            approver.auto = False
        else:
            approver.auto = not approver.auto
        self._emit(_t("tui.auto_on") if approver.auto else _t("tui.auto_off"))

    def _lang(self, argument: str) -> None:
        choice = argument.strip().lower()
        if not choice:
            self._emit(_t("cmd.lang_usage", lang=get_language()))
            return
        lang = set_language(choice)
        self._emit(_t("cmd.lang_set", lang=lang))

    def _copy(self, argument: str) -> None:
        count = int(argument) if argument.strip().isdigit() else 1
        replies = [
            message
            for message in self.ctx.agent.messages
            if isinstance(message, AssistantMessage) and message.text()
        ]
        if not replies:
            self._emit(_t("cmd.nothing_copy"))
            return
        if count < 1 or count > len(replies):
            self._emit(_t("cmd.only_n", n=len(replies)))
            return
        text = replies[-count].text()
        if copy_to_clipboard(text):
            self._emit(_t("cmd.copied", count=count))
        else:
            self._emit(_t("cmd.clipboard_failed"))

    def _trust(self, argument: str) -> None:
        manager = self.ctx.trust_manager
        if manager is None:
            self._emit(_t("cmd.trust_unavailable"))
            return
        trusted = argument.strip().lower() not in ("off", "no", "false", "untrust")
        manager.save(self.ctx.cwd, trusted)
        key = "cmd.trusted" if trusted else "cmd.untrusted"
        self._emit(_t(key, cwd=self.ctx.cwd))


__all__ = [
    "CommandContext",
    "Emit",
    "ExitSignal",
    "SessionPicker",
    "SlashCommands",
    "command_specs",
    "help_text",
]
