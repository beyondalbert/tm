from __future__ import annotations

import json

from tm.im.protocol import Cmd, Frame, ImMessage
from tm.im.realtime import RealtimeClient, _next_backoff


class RecordingClient(RealtimeClient):
    """RealtimeClient that captures outgoing frames instead of using a socket."""

    def __init__(self, on_message) -> None:
        super().__init__(
            "tok",
            tcp_addr=("localhost", 8090),
            on_message=on_message,
            get_cursors=lambda: {},
        )
        self.sent: list[tuple] = []

    async def _send_frame(self, cmd: int, req_id: int, payload: bytes = b"") -> None:
        self.sent.append((cmd, req_id, payload))


def _make_client(seen: list[ImMessage]) -> RecordingClient:
    async def on_message(message: ImMessage) -> None:
        seen.append(message)

    return RecordingClient(on_message)


def _msg(seq: int, conv: int = 1001, sender: int = 1001, text: str = "hi") -> ImMessage:
    return ImMessage(
        message_id=seq * 10,
        conv_id=conv,
        seq=seq,
        sender_id=sender,
        msg_type=1,
        content_json=json.dumps({"text": text}, ensure_ascii=False),
    )


async def test_contiguous_delivery_advances_cursor() -> None:
    seen: list[ImMessage] = []
    client = _make_client(seen)
    client._cursors = {1001: 0}
    for seq in (1, 2, 3):
        await client._deliver(_msg(seq))
    assert [m.seq for m in seen] == [1, 2, 3]
    assert client._cursors[1001] == 3


async def test_duplicate_is_ignored() -> None:
    seen: list[ImMessage] = []
    client = _make_client(seen)
    client._cursors = {1001: 2}
    await client._deliver(_msg(2))
    assert seen == []


async def test_gap_starts_sync_and_holds_message() -> None:
    seen: list[ImMessage] = []
    client = _make_client(seen)
    client._cursors = {1001: 3}
    await client._deliver(_msg(5))
    assert seen == []
    assert any(cmd == Cmd.SYNC for cmd, _, _ in client.sent)


async def test_ping_is_answered_with_pong() -> None:
    seen: list[ImMessage] = []
    client = _make_client(seen)
    await client._dispatch(Frame(int(Cmd.PING), 0))
    assert client.sent[-1][0] == Cmd.PONG


async def test_auth_ok_sets_identity() -> None:
    from tm.im.protocol import _enc_str, _enc_uint

    seen: list[ImMessage] = []
    client = _make_client(seen)
    payload = _enc_uint(1, 2002) + _enc_str(2, "weather_bot") + _enc_uint(3, 2)
    await client._dispatch(Frame(int(Cmd.AUTH_OK), 1, payload))
    assert client.actor_id == 2002
    assert client.handle == "weather_bot"
    assert client.heartbeat_sec == 30
    assert client._authenticated is True


def test_backoff_resets_after_authentication() -> None:
    # A successful authentication restarts the sequence at one second.
    assert _next_backoff(16.0, True) == (1.0, 2.0)
    assert _next_backoff(1.0, True) == (1.0, 2.0)
    # Unauthenticated failures grow exponentially and cap at 30 seconds.
    assert _next_backoff(1.0, False) == (1.0, 2.0)
    assert _next_backoff(16.0, False) == (16.0, 30.0)
    assert _next_backoff(30.0, False) == (30.0, 30.0)
