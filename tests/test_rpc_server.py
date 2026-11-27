from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from tm.rpc.protocol import (
    INTERNAL_ERROR,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    serialize,
)
from tm.rpc.server import HandlerFn, Request, RpcServer, Send


class StubHandler:
    """Minimal handler: enough surface to exercise the transport."""

    def __init__(self) -> None:
        self.closed = False
        self.order: list[Any] = []
        self.asked: list[Any] = []
        self._stop: asyncio.Event | None = None
        self._request: Request | None = None

    async def start(self, send: Send, request: Request) -> dict[str, HandlerFn]:
        self._stop = asyncio.Event()
        self._request = request
        return {
            "echo": self.echo,
            "slow": self.slow,
            "boom": self.boom,
            "ask": self.ask,
            "notify": self.notify,
            "shutdown": self.shutdown,
        }

    async def echo(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"echo": params}

    async def slow(self, params: dict[str, Any]) -> dict[str, Any]:
        self.order.append(("start", params["n"]))
        await asyncio.sleep(0.05)
        self.order.append(("end", params["n"]))
        return {"n": params["n"]}

    async def boom(self, params: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("kaboom")

    async def ask(self, params: dict[str, Any]) -> dict[str, Any]:
        assert self._request is not None
        answer = await self._request("client/question", {"q": "why"}, 1.0)
        self.asked.append(answer)
        return {"answer": answer}

    async def notify(self, params: dict[str, Any]) -> dict[str, Any]:
        self.asked.append(params)
        return {}

    async def shutdown(self, params: dict[str, Any]) -> dict[str, Any]:
        assert self._stop is not None
        self._stop.set()
        return {}

    async def close(self) -> None:
        self.closed = True

    async def wait_stopped(self) -> None:
        assert self._stop is not None
        await self._stop.wait()


class RpcHarness:
    """Drive an :class:`RpcServer` over in-memory streams."""

    def __init__(self, handler: Any) -> None:
        self.inbox: asyncio.Queue[str] = asyncio.Queue()
        self.messages: list[dict[str, Any]] = []
        self.diagnostics: list[str] = []
        self.handler = handler
        self.server = RpcServer(
            handler,
            read_line=self._read_line,
            write_line=self._write_line,
            diagnostic=self.diagnostics.append,
        )
        self._task: asyncio.Task[None] | None = None
        self._next_id = 0

    async def __aenter__(self) -> RpcHarness:
        self._task = asyncio.ensure_future(self.server.run())
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.feed("")
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(self._task, 2)

    async def _read_line(self) -> str:
        return await self.inbox.get()

    def _write_line(self, line: str) -> None:
        self.messages.append(json.loads(line))

    async def feed(self, line: str) -> None:
        self.inbox.put_nowait(line)

    async def call(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        await self.feed(
            serialize(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                }
            )
        )
        return await self.wait_response(request_id)

    async def result(self, method: str, params: dict[str, Any] | None = None) -> Any:
        response = await self.call(method, params)
        assert "result" in response, response
        return response["result"]

    async def error(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response = await self.call(method, params)
        assert "error" in response, response
        return dict(response["error"])

    async def wait_response(self, request_id: Any, timeout: float = 2.0) -> dict[str, Any]:
        return await self._wait(
            lambda msg: msg.get("id") == request_id
            and ("result" in msg or "error" in msg),
            timeout,
        )

    async def wait_notification(self, method: str, timeout: float = 2.0) -> dict[str, Any]:
        return await self._wait(lambda msg: msg.get("method") == method, timeout)

    async def send_client_result(self, request_id: Any, result: Any) -> None:
        await self.feed(serialize({"jsonrpc": "2.0", "id": request_id, "result": result}))

    async def _wait(self, predicate: Any, timeout: float) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            for message in list(self.messages):
                if predicate(message):
                    return message
            if loop.time() > deadline:
                raise AssertionError(f"no matching message within {timeout}s: {self.messages}")
            await asyncio.sleep(0.005)


async def test_request_round_trip() -> None:
    async with RpcHarness(StubHandler()) as harness:
        result = await harness.result("echo", {"value": 1})
        assert result == {"echo": {"value": 1}}


async def test_unknown_method() -> None:
    async with RpcHarness(StubHandler()) as harness:
        error = await harness.error("nope")
        assert error["code"] == METHOD_NOT_FOUND
        assert "nope" in error["message"]


async def test_handler_exception_becomes_internal_error() -> None:
    async with RpcHarness(StubHandler()) as harness:
        error = await harness.error("boom")
        assert error["code"] == INTERNAL_ERROR
        assert "RuntimeError: kaboom" in error["message"]
        assert any("kaboom" in line for line in harness.diagnostics)


async def test_parse_error_keeps_server_alive() -> None:
    async with RpcHarness(StubHandler()) as harness:
        await harness.feed("{not json")
        message = await harness._wait(lambda msg: "error" in msg, 2.0)
        assert message["id"] is None
        assert message["error"]["code"] == PARSE_ERROR

        assert await harness.result("echo", {"ok": True}) == {"echo": {"ok": True}}


async def test_notifications_get_no_response() -> None:
    async with RpcHarness(StubHandler()) as harness:
        await harness.feed(serialize({"jsonrpc": "2.0", "method": "echo", "params": {}}))
        assert await harness.result("echo", {}) == {"echo": {}}
        assert [msg for msg in harness.messages if "id" not in msg] == []


async def test_registered_notification_handler_runs_without_a_response() -> None:
    handler = StubHandler()
    async with RpcHarness(handler) as harness:
        await harness.feed(serialize({"method": "notify", "params": {"note": 1}}))
        while not handler.asked:
            await asyncio.sleep(0.005)
        assert handler.asked == [{"note": 1}]
        assert all("id" in message for message in harness.messages)


async def test_requests_run_concurrently() -> None:
    handler = StubHandler()
    async with RpcHarness(handler) as harness:
        first = asyncio.ensure_future(harness.result("slow", {"n": 1}))
        second = asyncio.ensure_future(harness.result("slow", {"n": 2}))
        assert await first == {"n": 1}
        assert await second == {"n": 2}
        assert [step for step, _ in handler.order] == ["start", "start", "end", "end"]


async def test_server_can_request_from_the_host() -> None:
    handler = StubHandler()
    async with RpcHarness(handler) as harness:
        pending = asyncio.ensure_future(harness.result("ask", {}))
        request = await harness.wait_notification("client/question")
        assert request["params"] == {"q": "why"}
        await harness.send_client_result(request["id"], {"a": 42})
        assert await pending == {"answer": {"a": 42}}
        assert handler.asked == [{"a": 42}]


async def test_shutdown_ends_the_server() -> None:
    handler = StubHandler()
    async with RpcHarness(handler) as harness:
        assert await harness.result("shutdown") == {}
        await asyncio.wait_for(harness._task, 2)  # type: ignore[arg-type]
        assert handler.closed is True


async def test_eof_closes_the_handler() -> None:
    handler = StubHandler()
    harness = RpcHarness(handler)
    harness._task = asyncio.ensure_future(harness.server.run())
    await harness.feed("")
    await asyncio.wait_for(harness._task, 2)
    assert handler.closed is True
