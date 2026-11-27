"""Python client for ``tm serve --stdio``.

A thin wrapper over the protocol: it launches the CLI as a subprocess, speaks
JSON-RPC, and exposes turns as awaitable objects. All permission decisions stay
inside the TM process; ``on_permission`` only answers questions TM already asked.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional, Sequence

from tm.rpc.protocol import serialize

DEFAULT_COMMAND: tuple = (sys.executable, "-m", "tm.cli.main", "serve", "--stdio")

PermissionHandler = Callable[[Dict[str, Any]], Awaitable[Optional[str]]]


class TmError(Exception):
    """An error returned by the server."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


@dataclass
class ChangedFile:
    path: str
    kind: str = "file"
    tool: str = ""
    summary: str = ""
    added: int | None = None
    removed: int | None = None
    diff: str | None = None
    exists: bool = True

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ChangedFile:
        return cls(
            path=str(payload.get("path", "")),
            kind=str(payload.get("kind", "file")),
            tool=str(payload.get("tool", "")),
            summary=str(payload.get("summary", "")),
            added=payload.get("added") if isinstance(payload.get("added"), int) else None,
            removed=(
                payload.get("removed") if isinstance(payload.get("removed"), int) else None
            ),
            diff=payload.get("diff") if isinstance(payload.get("diff"), str) else None,
            exists=bool(payload.get("exists", True)),
        )


@dataclass
class TurnResult:
    session_id: str | None
    turn_id: str
    status: str
    text: str = ""
    stop_reason: str | None = None
    error: str | None = None
    usage: dict[str, Any] | None = None
    changed_files: list[ChangedFile] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class _TurnState:
    def __init__(self, session_id: str | None, turn_id: str) -> None:
        self.session_id = session_id
        self.turn_id = turn_id
        self.events: list[dict[str, Any]] = []
        self.future: asyncio.Future | None = None
        self.end: dict[str, Any] | None = None


class Turn:
    """One ``prompt`` call. Await :meth:`wait` for the final answer."""

    def __init__(self, state: _TurnState) -> None:
        self._state = state

    @property
    def turn_id(self) -> str:
        return self._state.turn_id

    @property
    def session_id(self) -> str | None:
        return self._state.session_id

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self._state.events)

    async def wait(self, timeout: float | None = None) -> TurnResult:
        if self._state.end is None:
            loop = asyncio.get_running_loop()
            if self._state.future is None:
                self._state.future = loop.create_future()
            await asyncio.wait_for(asyncio.shield(self._state.future), timeout)
        payload = dict(self._state.end or {})
        return TurnResult(
            session_id=payload.get("sessionId"),
            turn_id=str(payload.get("turnId") or self._state.turn_id),
            status=str(payload.get("status") or "unknown"),
            text=str(payload.get("text") or ""),
            stop_reason=payload.get("stopReason"),
            error=payload.get("error"),
            usage=payload.get("usage") if isinstance(payload.get("usage"), dict) else None,
            changed_files=[
                ChangedFile.from_payload(item)
                for item in payload.get("changedFiles") or []
                if isinstance(item, dict)
            ],
            events=list(self._state.events),
            raw=payload,
        )


class TmSession:
    def __init__(self, client: TmClient, session_id: str) -> None:
        self._client = client
        self.id = session_id

    async def prompt(
        self, content: str, *, idempotency_key: str | None = None
    ) -> Turn:
        params: dict[str, Any] = {"content": content, "sessionId": self.id}
        if idempotency_key is not None:
            params["idempotencyKey"] = idempotency_key
        return await self._client._start_turn(params, self.id)

    async def info(self) -> dict[str, Any]:
        return await self._client.request("session/info", {"sessionId": self.id})

    async def compact(
        self, *, instructions: str | None = None, keep_recent: int | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"sessionId": self.id}
        if instructions is not None:
            params["instructions"] = instructions
        if keep_recent is not None:
            params["keepRecent"] = keep_recent
        return await self._client.request("session/compact", params)

    async def set_model(self, model: str) -> dict[str, Any]:
        """Switch this session's model, e.g. ``"deepseek/deepseek-chat"``."""
        return await self._client.request(
            "session/set-model", {"sessionId": self.id, "model": model}
        )

    async def undo(self, count: int = 1) -> dict[str, Any]:
        return await self._client.request(
            "session/undo", {"sessionId": self.id, "count": count}
        )

    async def fork(self, entry_id: str | None = None) -> TmSession:
        params: dict[str, Any] = {"sessionId": self.id}
        if entry_id is not None:
            params["entryId"] = entry_id
        info = await self._client.request("session/fork", params)
        return TmSession(self._client, info["sessionId"])

    async def close(self) -> dict[str, Any]:
        return await self._client.request("session/close", {"sessionId": self.id})


class TmClient:
    """A running ``tm serve --stdio`` process."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[Any, asyncio.Future[dict[str, Any]]] = {}
        self._states: dict[str, _TurnState] = {}
        self._events: list[dict[str, Any]] = []
        self._next_id = 0
        self._write_lock: asyncio.Lock | None = None
        self._closed = False
        self._permission_handler: PermissionHandler | None = None

    # -- construction ------------------------------------------------------
    @classmethod
    async def connect(
        cls,
        command: Sequence[str] | None = None,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        extra_args: Sequence[str] = (),
    ) -> TmClient:
        base = list(command or DEFAULT_COMMAND)
        executable, args = base[0], base[1:] + list(extra_args)
        process = await asyncio.create_subprocess_exec(
            executable,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,
            cwd=cwd,
            env={**os.environ, **env} if env else None,
        )
        client = cls(process)
        await client._start()
        return client

    async def _start(self) -> None:
        self._write_lock = asyncio.Lock()
        self._reader_task = asyncio.ensure_future(self._read_loop())

    async def __aenter__(self) -> TmClient:
        await self.initialize()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # -- transport ---------------------------------------------------------
    async def _read_loop(self) -> None:
        assert self._process.stdout is not None
        stream = self._process.stdout
        try:
            while True:
                raw = await stream.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                await self._dispatch(message)
        finally:
            self._fail_all(TmError(-32000, "the tm server exited"))

    async def _dispatch(self, message: dict[str, Any]) -> None:
        if "method" in message and "id" not in message:
            await self._handle_notification(message)
            return
        if "method" in message:
            await self._handle_server_request(message)
            return
        future = self._pending.pop(message.get("id"), None)
        if future is not None and not future.done():
            future.set_result(message)

    async def _handle_notification(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params") or {}
        if method == "event":
            turn_id = str(params.get("turnId") or "")
            state = self._states.get(turn_id)
            if state is not None:
                state.events.append(params.get("event") or {})
            self._events.append(params)
            return
        if method == "turn/end":
            turn_id = str(params.get("turnId") or "")
            state = self._states.setdefault(turn_id, _TurnState(params.get("sessionId"), turn_id))
            state.end = params
            if state.future is not None and not state.future.done():
                state.future.set_result(params)
            return
        if method == "permission/request":
            await self._answer_permission(params)

    async def _handle_server_request(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        if method == "session/request_permission":
            # ACP-shaped permission request: answer with the selected option.
            params = message.get("params") or {}
            decision: str | None = "deny"
            if self._permission_handler is not None:
                with contextlib.suppress(Exception):
                    decision = await self._permission_handler(params)
            if decision not in ("allow", "allow_scope", "deny"):
                decision = "deny"
            await self._write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "outcome": {"outcome": "selected", "optionId": decision}
                    },
                }
            )
            return
        await self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "unsupported client request"},
            }
        )

    async def _answer_permission(self, params: dict[str, Any]) -> None:
        decision: str | None = "deny"
        if self._permission_handler is not None:
            with contextlib.suppress(Exception):
                decision = await self._permission_handler(params)
        if decision not in ("allow", "allow_scope", "deny"):
            decision = "deny"
        request_params: dict[str, Any] = {
            "requestId": params.get("requestId"),
            "decision": decision,
        }
        if isinstance(params.get("sessionId"), str):
            request_params["sessionId"] = params["sessionId"]
        with contextlib.suppress(TmError, OSError):
            await self.request("approve", request_params)

    async def _write(self, payload: dict[str, Any]) -> None:
        if self._write_lock is None:
            raise TmError(-32000, "client is not started")
        if self._process.stdin is None:
            raise TmError(-32000, "the tm server is gone")
        async with self._write_lock:
            self._process.stdin.write((serialize(payload) + "\n").encode("utf-8"))
            await self._process.stdin.drain()

    def _fail_all(self, error: TmError) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
        for state in self._states.values():
            if state.future is not None and not state.future.done():
                state.future.set_exception(error)

    async def request(
        self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None
    ) -> Any:
        if self._closed:
            raise TmError(-32000, "client is closed")
        self._next_id += 1
        request_id = self._next_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        try:
            await self._write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                }
            )
            message = await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(request_id, None)
        if isinstance(message.get("error"), dict):
            error = message["error"]
            raise TmError(
                int(error.get("code", -32000)),
                str(error.get("message", "tm error")),
                error.get("data"),
            )
        return message.get("result")

    # -- api ---------------------------------------------------------------
    def on_permission(self, handler: PermissionHandler) -> None:
        """Answer ``permission/request`` questions. Default: deny."""
        self._permission_handler = handler

    async def initialize(self, client_name: str = "tm-sdk") -> dict[str, Any]:
        return await self.request("initialize", {"clientInfo": {"name": client_name}})

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    async def _start_turn(
        self, params: dict[str, Any], session_id: str | None
    ) -> Turn:
        reply = await self.request("prompt", params)
        turn_id = str(reply.get("turnId") or "")
        return Turn(self._states.setdefault(turn_id, _TurnState(session_id, turn_id)))

    async def prompt(
        self,
        content: str,
        *,
        session_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Turn:
        params: dict[str, Any] = {"content": content}
        if session_id is not None:
            params["sessionId"] = session_id
        if idempotency_key is not None:
            params["idempotencyKey"] = idempotency_key
        return await self._start_turn(params, session_id)

    async def create_session(
        self,
        *,
        cwd: str | None = None,
        access: str | None = None,
        tools: Sequence[str] | None = None,
        model: str | None = None,
    ) -> TmSession:
        params: dict[str, Any] = {}
        if cwd is not None:
            params["cwd"] = cwd
        if access is not None:
            params["access"] = access
        if tools is not None:
            params["tools"] = list(tools)
        if model is not None:
            params["model"] = model
        info = await self.request("session/create", params)
        return TmSession(self, info["sessionId"])

    async def open_session(self, path: str) -> TmSession:
        info = await self.request("session/open", {"path": path})
        return TmSession(self, info["sessionId"])

    async def sessions(self, cwd: str | None = None) -> list[dict[str, Any]]:
        params = {"cwd": cwd} if cwd is not None else {}
        result = await self.request("session/list", params)
        return list(result.get("sessions") or [])

    async def info(self, session_id: str | None = None) -> dict[str, Any]:
        params = {"sessionId": session_id} if session_id is not None else {}
        return await self.request("session/info", params)

    async def cancel(self, session_id: str | None = None) -> dict[str, Any]:
        params = {"sessionId": session_id} if session_id is not None else {}
        return await self.request("cancel", params)

    async def close(self, timeout: float = 5.0) -> None:
        if self._closed:
            return
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.request("shutdown"), timeout)
        self._closed = True
        with contextlib.suppress(Exception):
            if self._process.stdin is not None:
                self._process.stdin.close()
        if self._reader_task is not None:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._reader_task, timeout)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._process.wait(), timeout)
        if self._process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._process.kill()
            with contextlib.suppress(Exception):
                await self._process.wait()


__all__ = [
    "ChangedFile",
    "DEFAULT_COMMAND",
    "PermissionHandler",
    "TmClient",
    "TmError",
    "TmSession",
    "Turn",
    "TurnResult",
]
