"""Realtime client for tm_im.

Receives chat messages over a long connection, which can be either the native
TCP transport (a 4-byte big-endian length prefix followed by that many Frame
bytes) or a WebSocket (one binary message per Frame). The endpoint is chosen
explicitly: ``ws_url`` for WebSocket, otherwise ``tcp_addr`` (host:port).

It authenticates, keeps the connection alive, reconnects with jittered backoff,
and backfills gaps with ``SYNC`` after every reconnect (and whenever a gap is
detected).
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import random
import time
from collections.abc import Awaitable, Callable

from tm.im.protocol import (
    AuthResponse,
    Cmd,
    ErrorFrame,
    Frame,
    ImMessage,
    KickNotice,
    KickReason,
    decode_auth_response,
    decode_error,
    decode_frame,
    decode_kick,
    decode_push,
    decode_send_ack,
    decode_sync_end,
    decode_sync_response,
    encode_auth_request,
    encode_frame,
    encode_read_request,
    encode_sync_request,
)
from tm.im.websocket import WsConnection

_AUTH_ERRORS = {40101, 40102, 40103, 40104, 40105, 40106}
_MAX_CURSORS_PER_SYNC = 50
_IDLE_TIMEOUT = 90.0
_MAX_BACKOFF = 30.0


def _next_backoff(backoff: float, authenticated: bool) -> tuple[float, float]:
    """Return ``(delay, next_backoff)`` for the reconnect loop.

    A successful authentication means the server is reachable, so a later drop
    starts over at one second instead of inheriting a large backoff from earlier
    failures.
    """
    if authenticated:
        backoff = 1.0
    delay = min(_MAX_BACKOFF, backoff)
    return delay, min(_MAX_BACKOFF, backoff * 2)

MessageHandler = Callable[[ImMessage], Awaitable[None]]
CursorsProvider = Callable[[], dict[int, int]]
LogFn = Callable[[str], None]


class FatalRealtimeError(Exception):
    """The connection cannot be recovered (suspended account, other device)."""


class _Reconnect(Exception):
    """Close the current connection and reconnect."""


class _TcpTransport:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer

    async def send(self, data: bytes) -> None:
        self._writer.write(len(data).to_bytes(4, "big") + data)
        await self._writer.drain()

    async def recv(self) -> bytes | None:
        header = await self._reader.readexactly(4)
        length = int.from_bytes(header, "big")
        if length == 0:
            return b""
        return await self._reader.readexactly(length)

    def abort(self) -> None:
        self._writer.close()

    async def aclose(self) -> None:
        self._writer.close()
        with contextlib.suppress(Exception):
            await self._writer.wait_closed()


class _WsTransport:
    def __init__(self, connection: WsConnection) -> None:
        self._connection = connection

    async def send(self, data: bytes) -> None:
        await self._connection.send(data)

    async def recv(self) -> bytes | None:
        return await self._connection.recv()

    def abort(self) -> None:
        self._connection.abort()

    async def aclose(self) -> None:
        await self._connection.close()


class RealtimeClient:
    def __init__(
        self,
        token: str,
        *,
        tcp_addr: tuple[str, int] | None = None,
        ws_url: str | None = None,
        device_id: str = "tm",
        client_version: str = "tm",
        on_message: MessageHandler,
        get_cursors: CursorsProvider,
        on_ready: Callable[[AuthResponse], Awaitable[None]] | None = None,
        log: LogFn | None = None,
        sync_limit: int = 200,
    ) -> None:
        self.token = token
        self.tcp_addr = tcp_addr
        self.ws_url = ws_url
        self.device_id = device_id
        self.client_version = client_version
        self.on_message = on_message
        self.get_cursors = get_cursors
        self.on_ready = on_ready
        self.log = log or (lambda _message: None)
        self.sync_limit = sync_limit

        self.actor_id: int | None = None
        self.handle: str = ""
        self.heartbeat_sec = 30

        self._transport: _TcpTransport | _WsTransport | None = None
        self._stop = asyncio.Event()
        self._req = itertools.count(1)
        self._cursors: dict[int, int] = {}
        self._last_recv = time.monotonic()
        self._authenticated = False

        self._sync_batches: list[list[int]] = []
        self._sync_index = 0
        self._sync_active = False
        self._sync_pending = False
        self._reconnect_delay: float | None = None

    # -- public -----------------------------------------------------------
    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            self._reconnect_delay = None
            self._authenticated = False
            self._sync_active = False
            self._sync_pending = False
            try:
                await self._session()
            except FatalRealtimeError as error:
                self.log(f"stopping: {error}")
                return
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - reconnect on any failure
                if self._stop.is_set():
                    return
                self.log(f"connection lost: {type(error).__name__}: {error}")
            if self._stop.is_set():
                return
            delay, backoff = _next_backoff(backoff, self._authenticated)
            if self._reconnect_delay is not None:
                delay = self._reconnect_delay
            else:
                delay *= 1 + random.uniform(-0.3, 0.3)
            self.log(f"reconnecting in {delay:.1f}s")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)

    async def stop(self) -> None:
        self._stop.set()
        if self._transport is not None:
            self._transport.abort()

    def cursors(self) -> dict[int, int]:
        return dict(self._cursors)

    async def resync(self) -> None:
        await self._start_sync()

    # -- connection -------------------------------------------------------
    async def _open_transport(self) -> _TcpTransport | _WsTransport:
        if self.ws_url:
            return _WsTransport(await WsConnection.connect(self.ws_url))
        host, port = self.tcp_addr or ("localhost", 8090)
        reader, writer = await asyncio.open_connection(host, port)
        return _TcpTransport(reader, writer)

    async def _session(self) -> None:
        self._transport = await self._open_transport()
        self._cursors = dict(self.get_cursors())
        self._last_recv = time.monotonic()
        try:
            await self._send_frame(
                Cmd.AUTH,
                self._next_req(),
                encode_auth_request(self.token, self.client_version, self.device_id),
            )
            heartbeat = asyncio.create_task(self._heartbeat())
            try:
                await self._read_loop()
            finally:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
        finally:
            transport = self._transport
            self._transport = None
            if transport is not None:
                with contextlib.suppress(Exception):
                    await transport.aclose()

    async def _next_ready(self, frame: Frame) -> None:
        if frame.cmd == Cmd.AUTH_OK:
            info = decode_auth_response(frame.payload)
            self.actor_id = info.actor_id
            self.handle = info.handle
            self.heartbeat_sec = info.heartbeat_sec or 30
            self._authenticated = True
            self.log(f"authenticated as {info.handle or '?'} (actor_id={info.actor_id})")
            if self.on_ready is not None:
                await self.on_ready(info)
            await self._start_sync()

    # -- io ---------------------------------------------------------------
    async def _send_frame(self, cmd: int, req_id: int, payload: bytes = b"") -> None:
        transport = self._transport
        if transport is None:
            raise _Reconnect("not connected")
        await transport.send(encode_frame(cmd, req_id, payload))

    def _next_req(self) -> int:
        return next(self._req)

    async def _read_loop(self) -> None:
        transport = self._transport
        if transport is None:
            raise _Reconnect("not connected")
        while True:
            data = await transport.recv()
            if data is None:
                raise _Reconnect("closed by peer")
            self._last_recv = time.monotonic()
            if not data:
                continue
            frame = decode_frame(data)
            await self._dispatch(frame)

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_sec or 30)
            if time.monotonic() - self._last_recv > _IDLE_TIMEOUT:
                self.log("no server data for 90s; reconnecting")
                if self._transport is not None:
                    self._transport.abort()
                return
            try:
                await self._send_frame(Cmd.PING, 0)
            except Exception:  # noqa: BLE001 - a failed heartbeat means reconnect
                return

    # -- dispatch ---------------------------------------------------------
    async def _dispatch(self, frame: Frame) -> None:
        if frame.cmd == Cmd.AUTH_OK:
            await self._next_ready(frame)
        elif frame.cmd == Cmd.PING:
            await self._send_frame(Cmd.PONG, 0)
        elif frame.cmd == Cmd.PONG:
            return
        elif frame.cmd == Cmd.PUSH:
            await self._deliver(decode_push(frame.payload))
        elif frame.cmd == Cmd.SEND_ACK:
            ack = decode_send_ack(frame.payload)
            self.log(f"send ack conv={ack.conv_id} seq={ack.seq}")
        elif frame.cmd == Cmd.SYNC_RESP:
            await self._on_sync_response(frame)
        elif frame.cmd == Cmd.SYNC_END:
            end = decode_sync_end(frame.payload)
            if end.message:
                self.log(f"sync note: {end.message}")
            await self._on_sync_end()
        elif frame.cmd == Cmd.KICK:
            self._on_kick(decode_kick(frame.payload))
        elif frame.cmd == Cmd.ERROR:
            self._on_error(decode_error(frame.payload))
        else:
            self.log(f"unhandled frame cmd={int(frame.cmd)}")

    async def _deliver(self, message: ImMessage) -> None:
        if message.seq <= 0:
            return
        conv_id = message.conv_id
        last = self._cursors.get(conv_id, 0)
        if message.seq <= last:
            return
        if message.seq == last + 1:
            self._cursors[conv_id] = message.seq
            try:
                await self.on_message(message)
            except Exception as error:  # noqa: BLE001 - a handler bug must not drop the link
                self.log(f"message handler error: {error}")
            return
        # A gap: hold this message and let SYNC deliver the whole range in order.
        if self._sync_active:
            self._sync_pending = True
            return
        self._cursors.setdefault(conv_id, last)
        self.log(f"gap in conversation {conv_id}: have {last}, got {message.seq}; syncing")
        await self._start_sync()

    # -- sync -------------------------------------------------------------
    async def _start_sync(self, conversations: list[int] | None = None) -> None:
        if conversations is None:
            conversations = sorted(self._cursors)
        batches = [
            conversations[i : i + _MAX_CURSORS_PER_SYNC]
            for i in range(0, len(conversations), _MAX_CURSORS_PER_SYNC)
        ]
        self._sync_batches = batches
        self._sync_index = 0
        self._sync_active = True
        if batches:
            await self._send_sync_batch()
        else:
            self._sync_active = False

    async def _send_sync_batch(self) -> None:
        conversations = self._sync_batches[self._sync_index]
        cursors = {conv: self._cursors.get(conv, 0) for conv in conversations}
        await self._send_frame(
            Cmd.SYNC, self._next_req(), encode_sync_request(cursors, self.sync_limit)
        )

    async def _on_sync_response(self, frame: Frame) -> None:
        response = decode_sync_response(frame.payload)
        if response.truncated:
            self.log("sync truncated: server cannot backfill the full range")
        for message in response.messages:
            await self._deliver(message)
        if response.has_more:
            await self._send_sync_batch()
        # When has_more is false the server follows with CMD_SYNC_END.

    async def _on_sync_end(self) -> None:
        self._sync_index += 1
        if self._sync_index < len(self._sync_batches):
            await self._send_sync_batch()
            return
        self._sync_active = False
        if self._sync_pending:
            self._sync_pending = False
            await self._start_sync()

    def _on_kick(self, notice: KickNotice) -> None:
        detail = notice.detail or ""
        if notice.retry_after_ms:
            self._reconnect_delay = notice.retry_after_ms / 1000.0
        if notice.reason in (KickReason.OTHER_DEVICE, KickReason.SUSPENDED):
            raise FatalRealtimeError(f"kicked (reason={notice.reason}) {detail}".strip())
        self.log(f"kicked by server (reason={notice.reason}); reconnecting")
        raise _Reconnect("kicked")

    def _on_error(self, error: ErrorFrame) -> None:
        if error.code in _AUTH_ERRORS or error.code == 40301:
            raise FatalRealtimeError(f"[{error.code}] {error.message}")
        self.log(f"server error [{error.code}] {error.message}")

    async def send_read(self, conv_id: int, last_read_seq: int) -> None:
        await self._send_frame(
            Cmd.READ, self._next_req(), encode_read_request(conv_id, last_read_seq)
        )


__all__ = ["FatalRealtimeError", "RealtimeClient"]
