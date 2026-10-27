"""The bot: receive chat messages, run the TM agent, and reply.

Messages handed in by the realtime client are queued and processed by a small
pool of workers. Each conversation is serialized with its own lock and served
by one persistent TM session, so a conversation keeps its memory.

Every agent event of a turn is written to the log (turns, model output, tool
calls and their results), so ``tm im logs`` shows what the bot is doing for a
message. A message that arrives while its conversation is still busy is
answered immediately with ``busy_reply`` and then processed in order.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path
from typing import Awaitable, Callable

from tm.ai.types import AssistantMessage
from tm.core.agent import Agent
from tm.core.events import (
    AgentEndEvent,
    AgentEvent,
    AgentNoticeEvent,
    MessageEndEvent,
    MessageStartEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from tm.core.session import Session, SessionManager
from tm.im.client import ImApiError, ImClient
from tm.im.config import ImSettings
from tm.im.protocol import ImMessage, MsgType
from tm.im.state import ImState

AgentFactory = Callable[[int, Session], Agent]
MessageHandler = Callable[[ImMessage], Awaitable[None]]

_MAX_LOG_TEXT = 400
_MAX_LOG_ARGS = 200
_MAX_LOG_OUTPUT = 300


def _clip(text: str, limit: int) -> str:
    """Collapse whitespace and cut to ``limit`` characters for one log line."""
    collapsed = " ".join(str(text).split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _arguments(arguments: dict) -> str:
    try:
        text = json.dumps(arguments, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(arguments)
    return _clip(text, _MAX_LOG_ARGS)


def _event_lines(event: AgentEvent, tool_starts: dict[str, float]) -> list[str]:
    """Render one agent event as log lines (empty when there is nothing to say)."""
    if isinstance(event, AgentNoticeEvent):
        return [f"notice: {_clip(event.text, _MAX_LOG_TEXT)}"]
    if isinstance(event, TurnStartEvent):
        return [f"turn {event.turn} start"]
    if isinstance(event, TurnEndEvent):
        return [f"turn {event.turn} end"]
    if isinstance(event, MessageStartEvent):
        return ["model responding"] if isinstance(event.message, AssistantMessage) else []
    if isinstance(event, MessageEndEvent) and isinstance(event.message, AssistantMessage):
        message = event.message
        lines: list[str] = []
        thinking = _clip(message.thinking(), _MAX_LOG_TEXT)
        if thinking:
            lines.append(f"thinking: {thinking}")
        text = _clip(message.text(), _MAX_LOG_TEXT)
        if text:
            lines.append(f"assistant: {text}")
        details: list[str] = []
        if message.tool_calls:
            details.append(f"tool_calls={', '.join(call.name for call in message.tool_calls)}")
        details.append(f"stop={message.stop_reason or 'unknown'}")
        if message.usage is not None:
            details.append(
                f"tokens in={message.usage.input} out={message.usage.output}"
            )
        if message.error_message:
            details.append(f"error={_clip(message.error_message, _MAX_LOG_TEXT)}")
        lines.append(" | ".join(details))
        return lines
    if isinstance(event, ToolExecutionStartEvent):
        tool_starts[event.tool_call_id] = time.monotonic()
        return [f"tool {event.tool_name} start {_arguments(event.arguments)}"]
    if isinstance(event, ToolExecutionUpdateEvent):
        return [f"tool {event.tool_name} output: {_clip(event.text, _MAX_LOG_OUTPUT)}"]
    if isinstance(event, ToolExecutionEndEvent):
        started = tool_starts.pop(event.tool_call_id, None)
        elapsed = f" in {time.monotonic() - started:.1f}s" if started is not None else ""
        status = "error" if event.is_error else "ok"
        return [
            f"tool {event.tool_name} {status}{elapsed}: "
            f"{_clip(event.output, _MAX_LOG_OUTPUT)}"
        ]
    if isinstance(event, AgentEndEvent):
        return [
            f"agent finished stop={event.stop_reason or 'unknown'} "
            f"messages={len(event.messages)}"
        ]
    return []


class _TurnLog:
    """Agent listener that logs one conversation's turn progress.

    ``begin`` tags every line with the incoming message, so the log can be
    followed from receipt to reply; ``end`` stops logging between turns.
    """

    def __init__(self, log: Callable[[str], None]) -> None:
        self._log = log
        self._prefix = ""
        self._tool_starts: dict[str, float] = {}

    def begin(self, prefix: str) -> None:
        self._prefix = prefix
        self._tool_starts.clear()

    def end(self) -> None:
        self._prefix = ""

    async def __call__(self, event: AgentEvent) -> None:
        if not self._prefix:
            return
        for line in _event_lines(event, self._tool_starts):
            self._log(f"{self._prefix} {line}")


class ImBot:
    def __init__(
        self,
        *,
        settings: ImSettings,
        client: ImClient,
        state: ImState,
        agent_factory: AgentFactory,
        session_manager: SessionManager,
        log: Callable[[str], None] | None = None,
        workers: int = 2,
    ) -> None:
        self.settings = settings
        self.client = client
        self.state = state
        self.agent_factory = agent_factory
        self.session_manager = session_manager
        self.log = log or (lambda _message: None)
        self.workers = max(1, workers)

        self.actor_id: int | None = None
        self.handle = ""
        self._queue: asyncio.Queue[ImMessage] = asyncio.Queue()
        self._agents: dict[int, Agent] = {}
        self._progress: dict[int, _TurnLog] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._conv_types: dict[int, int] = {}
        self._inflight: dict[int, int] = {}
        self._tasks: list[asyncio.Task] = []

    # -- discovery --------------------------------------------------------
    async def discover(self) -> None:
        me = await self.client.me()
        self.actor_id = int(me.get("actor_id", 0))
        self.handle = str(me.get("handle", ""))
        for conv in await self.client.conversations():
            conv_id = int(conv["conv_id"])
            self._conv_types[conv_id] = int(conv.get("conv_type", 1))
            # First sight of a conversation starts at its tip, so pre-existing
            # history is not replayed as if it were new.
            if conv_id not in self.state.cursors:
                self.state.set_last_seq(conv_id, int(conv.get("last_seq", 0)))
        self.state.save()

    async def handle_friend_requests(self) -> None:
        mode = self.settings.auto_accept_friends
        if mode == "none":
            return
        try:
            requests = await self.client.friends_requests()
        except ImApiError as error:
            self.log(f"could not list friend requests: {error}")
            return
        for request in requests:
            sender = request.get("from_actor", {}) if isinstance(request, dict) else {}
            handle = str(sender.get("handle", "")) if isinstance(sender, dict) else ""
            allowed = mode == "all" or self._is_owner(handle)
            if not allowed:
                continue
            try:
                data = await self.client.accept_friend(int(request["request_id"]))
                self.log(f"accepted friend request from @{handle} (conv={data.get('conv_id')})")
            except ImApiError as error:
                self.log(f"could not accept request from @{handle}: {error}")

    def _is_owner(self, handle: str) -> bool:
        return handle in self.settings.owner_handles or f"@{handle}" in self.settings.owner_handles

    # -- queue / workers --------------------------------------------------
    async def enqueue(self, message: ImMessage) -> None:
        await self._queue.put(message)

    def start_workers(self) -> None:
        self._tasks = [asyncio.create_task(self._worker()) for _ in range(self.workers)]

    async def stop_workers(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []

    async def _worker(self) -> None:
        while True:
            message = await self._queue.get()
            try:
                await self.process(message)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - one bad message must not stop the bot
                self.log(f"error handling conv={message.conv_id} seq={message.seq}: {error}")
            finally:
                self._queue.task_done()

    # -- message handling -------------------------------------------------
    async def process(self, message: ImMessage) -> None:
        if message.msg_type != MsgType.TEXT:
            return
        if self.actor_id is not None and message.sender_id == self.actor_id:
            return
        text = message.text().strip()
        if not text:
            return

        conv_id = message.conv_id
        label = f"[{conv_id}#{message.seq}]"
        if message.seq <= self.state.last_seq(conv_id):
            return
        if not await self._should_reply(message, text):
            lock = self._locks.setdefault(conv_id, asyncio.Lock())
            async with lock:
                if message.seq > self.state.last_seq(conv_id):
                    self._advance(message)
            return

        # Claim a slot without awaiting: two workers must not both see an idle
        # conversation when messages arrive back to back.
        ahead = self._inflight.get(conv_id, 0)
        self._inflight[conv_id] = ahead + 1
        try:
            if ahead:
                self.log(f"{label} previous turn still running; sending busy notice")
                await self._send_busy(message)
            lock = self._locks.setdefault(conv_id, asyncio.Lock())
            async with lock:
                if message.seq <= self.state.last_seq(conv_id):
                    self.log(f"{label} already handled while waiting; skipped")
                    return
                self.log(f"<- {label} {text}")
                started = time.monotonic()
                await self._send_ack(message, text)
                agent = self._agent(conv_id)
                progress = self._progress[conv_id]
                progress.begin(label)
                try:
                    await agent.prompt(text)
                finally:
                    progress.end()
                reply = self._last_reply(agent)
                elapsed = time.monotonic() - started
                if reply:
                    self.log(f"{label} turn finished in {elapsed:.1f}s")
                    for chunk in self._split(reply, self.settings.reply_max_chars):
                        await self._send(conv_id, message.seq, chunk)
                else:
                    self.log(
                        f"no reply for conv={conv_id} seq={message.seq}: "
                        f"{self._failure(agent)} (after {elapsed:.1f}s)"
                    )
                self._advance(message)
                await self._mark_read(conv_id, message.seq)
        finally:
            remaining = self._inflight[conv_id] - 1
            if remaining > 0:
                self._inflight[conv_id] = remaining
            else:
                del self._inflight[conv_id]

    async def _should_reply(self, message: ImMessage, text: str) -> bool:
        conv_type = await self._conv_type(message.conv_id)
        if conv_type != 2:
            return True
        policy = self.settings.respond_in_groups
        if policy == "never":
            self.log(f"skip [{message.conv_id}#{message.seq}]: group policy is 'never'")
            return False
        if policy == "mention":
            handle = self.handle or ""
            if handle and f"@{handle}" in text:
                return True
            self.log(
                f"skip [{message.conv_id}#{message.seq}]: group message does not "
                f"mention @{handle or '?'}"
            )
            return False
        return True

    async def _conv_type(self, conv_id: int) -> int:
        if conv_id not in self._conv_types:
            try:
                info = await self.client.conversation(conv_id)
                self._conv_types[conv_id] = int(info.get("conv_type", 1))
            except ImApiError:
                self._conv_types[conv_id] = 1
        return self._conv_types[conv_id]

    def _advance(self, message: ImMessage) -> None:
        self.state.set_last_seq(message.conv_id, message.seq)
        self.state.save()

    async def _send_ack(self, message: ImMessage, text: str) -> None:
        """Reply immediately so the sender sees the message was received.

        The agent turn can take a while (or fail); this acknowledgment is
        best-effort and must never prevent the real reply.
        """
        ack = self._ack_text(text)
        if not ack:
            return
        with contextlib.suppress(ImApiError):
            # _send already logged the failure; the real reply may still work.
            await self._send(message.conv_id, message.seq, ack)

    async def _send_busy(self, message: ImMessage) -> None:
        """Tell the sender a previous turn is still running; best effort."""
        text = (self.settings.busy_reply or "").strip()
        if not text:
            return
        with contextlib.suppress(ImApiError):
            # _send already logged the failure; the message is still queued.
            await self._send(message.conv_id, message.seq, text)

    def _ack_text(self, text: str) -> str:
        template = (self.settings.ack_reply or "").strip()
        if not template:
            return ""
        preview = " ".join(text.split())
        limit = max(1, int(self.settings.ack_preview_chars))
        if len(preview) > limit:
            preview = preview[: limit - 1].rstrip() + "…"
        return template.replace("{preview}", preview)

    async def _send(self, conv_id: int, seq: int, text: str) -> None:
        try:
            await self.client.send_text(conv_id, text)
        except ImApiError as error:
            if error.code in (40003, 40304):
                self.log(f"cannot reply to conv={conv_id} seq={seq}: {error}")
                return
            self.log(f"failed to reply to conv={conv_id} seq={seq}: {error}")
            raise
        self.log(f"-> [{conv_id}#{seq}] {text}")

    async def _mark_read(self, conv_id: int, seq: int) -> None:
        with contextlib.suppress(ImApiError):
            await self.client.mark_read(conv_id, seq)

    # -- sessions / agents ------------------------------------------------
    def _agent(self, conv_id: int) -> Agent:
        agent = self._agents.get(conv_id)
        if agent is not None:
            return agent
        session = self._session(conv_id)
        agent = self.agent_factory(conv_id, session)
        progress = _TurnLog(self.log)
        agent.subscribe(progress)
        self._agents[conv_id] = agent
        self._progress[conv_id] = progress
        return agent

    def _session(self, conv_id: int) -> Session:
        path = self.state.session_path(conv_id)
        if path is not None and path.exists():
            return self.session_manager.open(path)
        session = self.session_manager.create(cwd=Path.cwd())
        self.state.set_session_path(conv_id, session.path)
        self.state.save()
        return session

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _last_reply(agent: Agent) -> str:
        for message in reversed(agent.messages):
            if isinstance(message, AssistantMessage):
                text = message.text().strip()
                if text:
                    return text
        return ""

    @staticmethod
    def _failure(agent: Agent) -> str:
        """Why the last turn produced no reply text (for logging)."""
        for message in reversed(agent.messages):
            if isinstance(message, AssistantMessage):
                if message.error_message:
                    return message.error_message
                return f"stop_reason={message.stop_reason or 'unknown'} (empty reply)"
        return f"stop_reason={agent.last_stop_reason or 'unknown'}"

    @staticmethod
    def _split(text: str, limit: int) -> list[str]:
        if limit <= 0 or len(text) <= limit:
            return [text]
        chunks: list[str] = []
        current = ""
        for paragraph in text.split("\n"):
            candidate = paragraph if not current else f"{current}\n{paragraph}"
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                chunks.append(current)
                current = ""
            while len(paragraph) > limit:
                chunks.append(paragraph[:limit])
                paragraph = paragraph[limit:]
            current = paragraph
        if current:
            chunks.append(current)
        return chunks or [text[:limit]]


__all__ = ["AgentFactory", "ImBot"]
