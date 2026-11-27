"""JSON-RPC transport for ``tm serve --stdio``.

The server knows nothing about agents: it parses lines, dispatches requests and
notifications to the handler's method table, writes responses, and can call back
into the host (``request``). Methods run as separate tasks so ``cancel`` can
arrive while a turn is still running.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import threading
import traceback
from typing import Any, Awaitable, Callable, Dict, Protocol

from tm.rpc.protocol import (
    APPROVAL_TIMEOUT,
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    NOTIFICATION,
    REQUEST,
    RESPONSE,
    RpcError,
    error_response,
    parse_message,
    result_response,
    serialize,
)

Send = Callable[[Dict[str, Any]], Awaitable[None]]
Request = Callable[..., Awaitable[Any]]
HandlerFn = Callable[[Dict[str, Any]], Awaitable[Any]]
ReadLine = Callable[[], Awaitable[str]]
WriteLine = Callable[[str], None]
Diagnostic = Callable[[str], None]


class Handler(Protocol):
    async def start(self, send: Send, request: Request) -> dict[str, HandlerFn]: ...

    async def close(self) -> None: ...

    async def wait_stopped(self) -> None: ...


class ThreadedLineReader:
    """Read lines off stdin on a daemon thread and hand them to the loop.

    A daemon thread does not block interpreter shutdown, and a cancelled read
    does not leave the default executor waiting on a blocked ``readline``
    (which is what happens when a host sends ``shutdown`` and keeps stdin open).
    """

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        #: Created by :meth:`start`, inside the running loop.
        self._queue: asyncio.Queue[str] | None = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._queue = asyncio.Queue()
        thread = threading.Thread(target=self._pump, args=(loop,), daemon=True)
        thread.start()

    def _pump(self, loop: asyncio.AbstractEventLoop) -> None:
        assert self._queue is not None
        while True:
            try:
                line = self._stream.readline()
            except Exception:
                line = ""
            loop.call_soon_threadsafe(self._queue.put_nowait, line)
            if line == "":
                return

    async def read_line(self) -> str:
        assert self._queue is not None
        return await self._queue.get()


def _stdout_write(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _stderr_diagnostic(text: str) -> None:
    sys.stderr.write(text if text.endswith("\n") else text + "\n")
    sys.stderr.flush()


class RpcServer:
    def __init__(
        self,
        handler: Handler,
        *,
        read_line: ReadLine | None = None,
        write_line: WriteLine | None = None,
        diagnostic: Diagnostic | None = None,
    ) -> None:
        self._handler = handler
        self._read_line = read_line
        self._write_line = write_line or _stdout_write
        self._diagnostic = diagnostic or _stderr_diagnostic
        self._methods: dict[str, HandlerFn] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._outbound: dict[str, asyncio.Future[Any]] = {}
        self._write_lock: asyncio.Lock | None = None
        self._next_outbound = 0
        self._closed = False

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        self._lock()
        if self._read_line is None:
            reader = ThreadedLineReader(sys.stdin)
            reader.start(loop)
            self._read_line = reader.read_line
        self._methods = await self._handler.start(self.send, self.request)
        try:
            while not self._closed:
                line = await self._next_line()
                if line is None:
                    break
                self._dispatch_line(line)
        finally:
            await self._shutdown()

    async def _next_line(self) -> str | None:
        """The next input line, or ``None`` when stdin closed or the host stopped us."""
        assert self._read_line is not None
        read_task: asyncio.Task[str] = asyncio.ensure_future(self._read_line())
        stop_task: asyncio.Task[None] = asyncio.ensure_future(
            self._handler.wait_stopped()
        )
        done, pending = await asyncio.wait(
            {read_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if stop_task in done:
            return None
        line = read_task.result()
        return None if line == "" else line

    def _dispatch_line(self, line: str) -> None:
        if not line.strip():
            return
        try:
            message = parse_message(line)
        except RpcError as exc:
            # A malformed notification gets no answer; we cannot tell, so answer.
            self._spawn(self.send(error_response(None, exc.code, exc.message, exc.data)))
            return
        if message.kind is RESPONSE:
            future = self._outbound.get(str(message.request_id))
            if future is not None and not future.done():
                future.set_result(message)
            return
        if message.kind is NOTIFICATION:
            # Notifications never get a response, but a registered handler may
            # still act on them (ACP sends ``session/cancel`` this way).
            if message.method in self._methods:
                self._spawn(self._dispatch(None, message.method, message.params, False))
            return
        if message.kind is not REQUEST:
            self._spawn(
                self.send(error_response(None, INVALID_REQUEST, "unknown message kind"))
            )
            return
        self._spawn(
            self._dispatch(message.request_id, message.method, message.params, True)
        )

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _dispatch(
        self,
        request_id: Any,
        method: str,
        params: dict[str, Any],
        respond: bool,
    ) -> None:
        handler_fn = self._methods.get(method)
        if handler_fn is None:
            if respond:
                await self.send(
                    error_response(
                        request_id, METHOD_NOT_FOUND, f"unknown method: {method}"
                    )
                )
            return
        try:
            result = await handler_fn(params)
        except RpcError as exc:
            if respond:
                await self.send(
                    error_response(request_id, exc.code, exc.message, exc.data)
                )
            else:
                self._diagnostic(f"tm: {method} failed: {exc.message}")
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._diagnostic(traceback.format_exc())
            if respond:
                await self.send(
                    error_response(
                        request_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"
                    )
                )
            return
        if respond:
            await self.send(result_response(request_id, result))

    def _lock(self) -> asyncio.Lock:
        # Created on demand: a host may call ``request`` before ``run`` starts.
        if self._write_lock is None:
            self._write_lock = asyncio.Lock()
        return self._write_lock

    async def send(self, payload: dict[str, Any]) -> None:
        """Write one message. Safe to call from any task."""
        line = serialize(payload)
        async with self._lock():
            try:
                self._write_line(line)
            except OSError as exc:
                self._diagnostic(f"tm serve: write failed: {exc}")
                self._closed = True

    async def request(
        self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None
    ) -> Any:
        """Call the host and wait for its answer."""
        self._next_outbound += 1
        request_id = f"tm-{self._next_outbound}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._outbound[request_id] = future
        try:
            await self.send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                }
            )
            try:
                message = await asyncio.wait_for(future, timeout)
            except asyncio.TimeoutError:
                raise RpcError(APPROVAL_TIMEOUT, f"{method} timed out") from None
        finally:
            self._outbound.pop(request_id, None)
        if message.error is not None:
            raise RpcError(
                int(message.error.get("code", INTERNAL_ERROR)),
                str(message.error.get("message", "host error")),
                message.error.get("data"),
            )
        return message.result

    async def _shutdown(self) -> None:
        for future in self._outbound.values():
            if not future.done():
                future.cancel()
        self._outbound.clear()
        current = asyncio.current_task()
        pending = [task for task in self._tasks if task is not current]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await self._handler.close()


__all__ = [
    "Handler",
    "HandlerFn",
    "RpcServer",
    "Send",
    "Request",
    "ThreadedLineReader",
]
