from __future__ import annotations

import json
from pathlib import Path

from tm.ai.event_stream import EventStream
from tm.ai.types import (
    AssistantMessage,
    DoneEvent,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
)
from tm.core.agent import Agent
from tm.core.session import Session, SessionManager
from tm.im.bot import ImBot
from tm.im.client import ImApiError, ImClient
from tm.im.config import ImSettings
from tm.im.protocol import ImMessage
from tm.im.state import ImState

MODEL = Model(id="fake", provider="fake")


def _stream(text: str) -> EventStream:
    message = AssistantMessage(content=[TextContent(text=text)], stop_reason="stop")
    stream: EventStream = EventStream()
    stream.push(StartEvent(partial=message))
    stream.push(TextDeltaEvent(partial=message, delta=text))
    stream.push(DoneEvent(partial=message, message=message))
    stream.end(message)
    return stream


def _factory(text: str):
    def factory(_conv_id: int, session: Session) -> Agent:
        return Agent(MODEL, stream_fn=lambda m, c, o: _stream(text), session=session)

    return factory


def _error_factory(message: str):
    def factory(_conv_id: int, session: Session) -> Agent:
        def stream_fn(_m, _c, _o) -> EventStream:
            assistant = AssistantMessage(
                content=[], stop_reason="error", error_message=message
            )
            stream: EventStream = EventStream()
            stream.push(StartEvent(partial=assistant))
            stream.push(DoneEvent(partial=assistant, message=assistant))
            stream.end(assistant)
            return stream

        return Agent(MODEL, stream_fn=stream_fn, session=session)

    return factory


class FakeClient(ImClient):
    def __init__(self, conv_type: int = 1) -> None:
        super().__init__("http://x/v1", "k")
        self.sent: list[tuple[int, str]] = []
        self.reads: list[tuple[int, int]] = []
        self.types: dict[int, int] = {}
        self._default_type = conv_type
        self.fail_send_prefix: str | None = None

    async def me(self) -> dict:
        return {"actor_id": 2002, "handle": "weather_bot"}

    async def conversations(self, limit: int = 50) -> list[dict]:
        return []

    async def conversation(self, conv_id: int) -> dict:
        return {"conv_id": conv_id, "conv_type": self.types.get(conv_id, self._default_type)}

    async def send_text(
        self,
        conv_id: int,
        text: str,
        *,
        client_msg_id: str | None = None,
        reply_to: int | None = None,
    ) -> dict:
        if self.fail_send_prefix and text.startswith(self.fail_send_prefix):
            raise ImApiError(50001, "send rejected")
        self.sent.append((conv_id, text))
        return {"seq": 1}

    async def mark_read(self, conv_id: int, last_read_seq: int) -> dict:
        self.reads.append((conv_id, last_read_seq))
        return {"conv_id": conv_id}

    async def friends_requests(self, direction: str = "incoming", status: str = "pending") -> list[dict]:
        return []


def _bot(
    tmp_path: Path,
    *,
    text: str = "pong",
    conv_type: int = 1,
    respond: str = "always",
    ack_reply: str = "",
    factory=None,
    log: list[str] | None = None,
) -> tuple[ImBot, FakeClient]:
    settings = ImSettings(respond_in_groups=respond, ack_reply=ack_reply)
    client = FakeClient(conv_type)
    state = ImState(tmp_path / "state.json")
    manager = SessionManager(tmp_path / "sessions")
    bot = ImBot(
        settings=settings,
        client=client,
        state=state,
        agent_factory=factory or _factory(text),
        session_manager=manager,
        log=log.append if log is not None else None,
    )
    bot.actor_id = 2002
    bot.handle = "weather_bot"
    return bot, client


def _msg(
    seq: int, *, sender: int = 1001, text: str = "hello", conv: int = 1001, msg_type: int = 1
) -> ImMessage:
    return ImMessage(
        message_id=seq * 10,
        conv_id=conv,
        seq=seq,
        sender_id=sender,
        msg_type=msg_type,
        content_json=json.dumps({"text": text}, ensure_ascii=False),
    )


async def test_replies_and_advances_cursor(tmp_path: Path) -> None:
    bot, client = _bot(tmp_path)
    await bot.process(_msg(2))
    assert client.sent == [(1001, "pong")]
    assert bot.state.last_seq(1001) == 2


async def test_skips_own_message(tmp_path: Path) -> None:
    bot, client = _bot(tmp_path)
    await bot.process(_msg(2, sender=2002))
    assert client.sent == []


async def test_skips_non_text(tmp_path: Path) -> None:
    bot, client = _bot(tmp_path)
    await bot.process(_msg(2, msg_type=2))
    assert client.sent == []


async def test_dedupes_processed_seq(tmp_path: Path) -> None:
    bot, client = _bot(tmp_path)
    bot.state.set_last_seq(1001, 5)
    await bot.process(_msg(4))
    assert client.sent == []


async def test_group_mention_policy(tmp_path: Path) -> None:
    bot, client = _bot(tmp_path, conv_type=2, respond="mention")
    await bot.process(_msg(2, text="hello everyone"))
    assert client.sent == []
    await bot.process(_msg(3, text="@weather_bot hello"))
    assert client.sent == [(1001, "pong")]


async def test_group_never_policy(tmp_path: Path) -> None:
    bot, client = _bot(tmp_path, conv_type=2, respond="never")
    await bot.process(_msg(2, text="@weather_bot hi"))
    assert client.sent == []


async def test_chunks_long_reply(tmp_path: Path) -> None:
    bot, client = _bot(tmp_path, text="x" * 50)
    bot.settings.reply_max_chars = 20
    await bot.process(_msg(2))
    assert len(client.sent) >= 3
    assert all(len(text) <= 20 for _, text in client.sent)


async def test_sends_ack_before_reply(tmp_path: Path) -> None:
    bot, client = _bot(tmp_path, ack_reply="收到：{preview}")
    await bot.process(_msg(2, text="帮我查一下天气"))
    assert client.sent == [(1001, "收到：帮我查一下天气"), (1001, "pong")]


async def test_ack_truncates_long_preview(tmp_path: Path) -> None:
    bot, client = _bot(tmp_path, ack_reply="got: {preview}")
    bot.settings.ack_preview_chars = 6
    await bot.process(_msg(2, text="one two three"))
    assert client.sent[0] == (1001, "got: one t…")


async def test_ack_is_skipped_when_disabled(tmp_path: Path) -> None:
    bot, client = _bot(tmp_path, ack_reply="")
    await bot.process(_msg(2))
    assert client.sent == [(1001, "pong")]


async def test_ack_failure_does_not_block_reply(tmp_path: Path) -> None:
    bot, client = _bot(tmp_path, ack_reply="收到：{preview}")
    client.fail_send_prefix = "收到"
    await bot.process(_msg(2))
    assert client.sent == [(1001, "pong")]
    assert bot.state.last_seq(1001) == 2


async def test_session_is_reused(tmp_path: Path) -> None:
    bot, _ = _bot(tmp_path)
    await bot.process(_msg(2))
    first = bot.state.session_path(1001)
    assert first is not None
    await bot.process(_msg(3))
    assert bot.state.session_path(1001) == first


async def test_logs_outgoing_reply(tmp_path: Path) -> None:
    lines: list[str] = []
    bot, client = _bot(tmp_path, text="pong", log=lines)
    await bot.process(_msg(2))
    assert client.sent == [(1001, "pong")]
    assert any("-> [1001#2] pong" in line for line in lines)


async def test_logs_error_when_no_reply(tmp_path: Path) -> None:
    lines: list[str] = []
    bot, client = _bot(tmp_path, factory=_error_factory("missing api key"), log=lines)
    await bot.process(_msg(2))
    assert client.sent == []
    assert any("no reply" in line and "missing api key" in line for line in lines)
