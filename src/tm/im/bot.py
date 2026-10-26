"""The bot: receive chat messages, run the TM agent, and reply.

Messages handed in by the realtime client are queued and processed by a small
pool of workers. Each conversation is serialized with its own lock and served
by one persistent TM session, so a conversation keeps its memory.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from pathlib import Path

from tm.ai.types import AssistantMessage
from tm.core.agent import Agent
from tm.core.session import Session, SessionManager
from tm.im.client import ImApiError, ImClient
from tm.im.config import ImSettings
from tm.im.protocol import ImMessage, MsgType
from tm.im.state import ImState

AgentFactory = Callable[[int, Session], Agent]
MessageHandler = Callable[[ImMessage], Awaitable[None]]


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
        self._locks: dict[int, asyncio.Lock] = {}
        self._conv_types: dict[int, int] = {}
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

        lock = self._locks.setdefault(message.conv_id, asyncio.Lock())
        async with lock:
            if message.seq <= self.state.last_seq(message.conv_id):
                return
            if not await self._should_reply(message, text):
                self._advance(message)
                return

            self.log(f"<- [{message.conv_id}#{message.seq}] {text}")
            await self._send_ack(message, text)
            agent = self._agent(message.conv_id)
            await agent.prompt(text)
            reply = self._last_reply(agent)
            if reply:
                for chunk in self._split(reply, self.settings.reply_max_chars):
                    await self._send(message.conv_id, message.seq, chunk)
            else:
                self.log(
                    f"no reply for conv={message.conv_id} seq={message.seq}: "
                    f"{self._failure(agent)}"
                )
            self._advance(message)
            await self._mark_read(message.conv_id, message.seq)

    async def _should_reply(self, message: ImMessage, text: str) -> bool:
        conv_type = await self._conv_type(message.conv_id)
        if conv_type != 2:
            return True
        policy = self.settings.respond_in_groups
        if policy == "never":
            return False
        if policy == "mention":
            handle = self.handle or ""
            return bool(handle) and f"@{handle}" in text
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
        self._agents[conv_id] = agent
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
