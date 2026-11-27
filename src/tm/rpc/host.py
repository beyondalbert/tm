"""JSON-RPC methods, sessions, and turn lifecycle for ``tm serve --stdio``.

Every method maps onto an existing object: ``Agent`` for turns, ``Session`` for
persistence, ``Journal`` for undo and per-turn diffs, and the approval bridge for
permission prompts. Nothing here decides a permission; the checker in
``tm.permissions`` stays authoritative.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from tm import __version__
from tm.ai.types import AssistantMessage, Message
from tm.core.agent import Agent
from tm.core.session import Session, SessionManager
from tm.core.storage import value
from tm.host import detect_host
from tm.permissions.approval import Approver
from tm.rpc.approver import DECISIONS, RpcApprover
from tm.rpc.changes import collect_changes
from tm.rpc.protocol import (
    BUSY,
    INVALID_PARAMS,
    NO_SESSION,
    NO_TURN,
    PROTOCOL_VERSION,
    SESSION_LIMIT,
    UNKNOWN_SESSION,
    UNSUPPORTED,
    RpcError,
    notification,
    optional_bool,
    optional_int,
    optional_str,
    optional_str_list,
    require_str,
)
from tm.rpc.server import HandlerFn, Request, Send
from tm.safety import Journal, apply_undo

ACCESS_MODES = ("normal", "read-only", "dry-run")

METHODS = (
    "approve",
    "cancel",
    "followUp",
    "initialize",
    "prompt",
    "session/close",
    "session/compact",
    "session/create",
    "session/delete",
    "session/fork",
    "session/info",
    "session/list",
    "session/open",
    "session/set-model",
    "session/undo",
    "shutdown",
    "steer",
)


@dataclass
class SessionOptions:
    cwd: Path
    access: str = "normal"
    tools: list[str] | None = None
    #: ``provider/model`` or a model id; ``None`` means the process default.
    model: str | None = None
    #: Extra system-prompt text supplied by the host product.
    instructions: str | None = None


@dataclass
class BuiltSession:
    agent: Agent
    approver: Approver | None = None
    journal: Journal | None = None
    #: Non-fatal configuration problems, e.g. a provider without an API key.
    warnings: list[str] = field(default_factory=list)


#: Builds an agent for one session. ``wire`` lets the approval bridge call back
#: into the host without knowing the transport.
SessionBuilder = Callable[
    [SessionOptions, Optional[Session], str, "Wire"], BuiltSession
]


class Wire:
    """The host's view of the connection, bound by the server at start."""

    def __init__(self) -> None:
        self._send: Send | None = None
        self._request: Request | None = None

    def bind(self, send: Send, request: Request) -> None:
        self._send = send
        self._request = request

    async def send(self, payload: dict[str, Any]) -> None:
        if self._send is None:
            raise RuntimeError("RPC transport is not ready")
        await self._send(payload)

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        if self._request is None:
            raise RuntimeError("RPC transport is not ready")
        return await self._request(method, params, timeout)


def _last_text(messages: list[Message]) -> str:
    for message in reversed(messages):
        if isinstance(message, AssistantMessage):
            return message.text() or ""
    return ""


class SessionRuntime:
    def __init__(
        self,
        session_id: str,
        *,
        session: Session | None,
        agent: Agent,
        approver: Approver | None,
        journal: Journal | None,
        options: SessionOptions,
    ) -> None:
        self.id = session_id
        self.session = session
        self.agent = agent
        self.approver = approver
        self.journal = journal
        self.options = options
        self.turn: asyncio.Task[None] | None = None
        self.turn_id = ""
        self.turn_started = 0.0
        self.end_future: asyncio.Future[dict[str, Any]] | None = None
        self.unsubscribe: Callable[[], None] | None = None
        self.memory_idempotency: dict[str, dict[str, Any]] = {}
        self.warnings: list[str] = []

    # -- durable idempotency ---------------------------------------------
    @staticmethod
    def _address(key: str) -> Any:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        return value("rpc.idempotency", digest)

    def idempotency_get(self, key: str) -> dict[str, Any] | None:
        if self.session is None:
            return self.memory_idempotency.get(key)
        with contextlib.suppress(Exception):
            stored = self.session.storage.get_value(self._address(key))
            if isinstance(stored, dict):
                return stored
        return None

    def idempotency_set(self, key: str, record: dict[str, Any]) -> None:
        if self.session is None:
            self.memory_idempotency[key] = record
            return
        with contextlib.suppress(Exception):
            self.session.storage.set_value(self._address(key), record)


class ServerHost:
    """Serves one or more sessions over one RPC connection."""

    def __init__(
        self,
        builder: SessionBuilder,
        *,
        manager: SessionManager | None = None,
        mode: str = "policy",
        max_sessions: int = 4,
        initial: SessionOptions | None = None,
        name: str = "tm",
        version: str = __version__,
        extra_methods: dict[str, HandlerFn] | None = None,
        model_switcher: Callable[[SessionRuntime, str], dict[str, Any]] | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        self._builder = builder
        self._manager = manager
        self._mode = mode
        self._max_sessions = max(1, int(max_sessions))
        self._name = name
        self._version = version
        self._wire = Wire()
        self._send: Send | None = None
        self._stop: asyncio.Event | None = None
        self._seq = 0
        self._sessions: dict[str, SessionRuntime] = {}
        self._default_id: str | None = None
        self._extra_methods = dict(extra_methods or {})
        self._model_switcher = model_switcher
        self._warnings = list(warnings or [])
        if initial is not None:
            self._create(initial)

    # -- lifecycle --------------------------------------------------------
    async def start(self, send: Send, request: Request) -> dict[str, HandlerFn]:
        self._send = send
        # Created here, inside the running loop, so a host that constructs the
        # object elsewhere cannot bind the event to a different loop.
        self._stop = asyncio.Event()
        self._wire.bind(send, request)
        for runtime in self._sessions.values():
            self._subscribe(runtime)
        methods: dict[str, HandlerFn] = {
            "initialize": self.initialize,
            "prompt": self.prompt,
            "cancel": self.cancel,
            "steer": self.steer,
            "followUp": self.follow_up,
            "approve": self.approve,
            "session/info": self.session_info,
            "session/create": self.session_create,
            "session/open": self.session_open,
            "session/list": self.session_list,
            "session/fork": self.session_fork,
            "session/compact": self.session_compact,
            "session/undo": self.session_undo,
            "session/close": self.session_close,
            "session/delete": self.session_delete,
            "session/set-model": self.session_set_model,
            "shutdown": self.shutdown,
        }
        methods.update(self._extra_methods)
        return methods

    async def close(self) -> None:
        for runtime in list(self._sessions.values()):
            turn = runtime.turn
            if turn is not None and not turn.done():
                runtime.agent.abort()
                turn.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await turn
            if runtime.unsubscribe is not None:
                runtime.unsubscribe()
                runtime.unsubscribe = None
            if runtime.session is not None:
                with contextlib.suppress(Exception):
                    runtime.session.storage.close()
        self._sessions.clear()

    def rebind_provider(self, provider_id: str, provider: Any) -> list[str]:
        """Point every session using ``provider_id`` at a rebuilt provider."""
        rebound: list[str] = []
        for runtime in self._sessions.values():
            if runtime.agent.model.provider == provider_id:
                runtime.agent.provider = provider
                rebound.append(runtime.id)
        return rebound

    async def wait_stopped(self) -> None:
        if self._stop is None:
            return
        await self._stop.wait()

    # -- sessions ---------------------------------------------------------
    def _create(
        self, options: SessionOptions, *, session: Session | None = None
    ) -> SessionRuntime:
        if len(self._sessions) >= self._max_sessions:
            raise RpcError(
                SESSION_LIMIT,
                f"session limit reached ({self._max_sessions})",
                {"maxSessions": self._max_sessions},
            )
        if session is None and self._manager is not None:
            session = self._manager.create(cwd=options.cwd)
        session_id = (
            session.id if session is not None else f"mem-{uuid.uuid4().hex[:8]}"
        )
        try:
            built = self._builder(options, session, session_id, self._wire)
        except RpcError:
            if session is not None:
                with contextlib.suppress(Exception):
                    session.storage.close()
            raise
        except ValueError as exc:
            if session is not None:
                with contextlib.suppress(Exception):
                    session.storage.close()
            raise RpcError(INVALID_PARAMS, str(exc)) from exc
        runtime = SessionRuntime(
            session_id,
            session=session,
            agent=built.agent,
            approver=built.approver,
            journal=built.journal,
            options=options,
        )
        runtime.warnings = list(built.warnings)
        self._sessions[session_id] = runtime
        if self._default_id is None:
            self._default_id = session_id
        if self._send is not None:
            self._subscribe(runtime)
        return runtime

    def _subscribe(self, runtime: SessionRuntime) -> None:
        if runtime.unsubscribe is not None:
            return

        async def listener(event: Any) -> None:
            await self._on_event(runtime, event)

        runtime.unsubscribe = runtime.agent.subscribe(listener)

    def _resolve(self, params: dict[str, Any]) -> SessionRuntime:
        session_id = optional_str(params, "sessionId") or self._default_id
        if session_id is None or not self._sessions:
            raise RpcError(NO_SESSION, "no session is open")
        runtime = self._sessions.get(session_id)
        if runtime is None:
            raise RpcError(
                UNKNOWN_SESSION,
                f"unknown session: {session_id}",
                {"sessionId": session_id},
            )
        return runtime

    def _info(self, runtime: SessionRuntime) -> dict[str, Any]:
        return {
            "sessionId": runtime.id,
            "id": runtime.id,
            "path": str(runtime.session.path) if runtime.session is not None else None,
            "cwd": str(runtime.options.cwd),
            "access": runtime.options.access,
            "persisted": runtime.session is not None,
            "messageCount": len(runtime.agent.messages),
            "tools": [tool.name for tool in runtime.agent.tools],
            "model": {
                "provider": runtime.agent.model.provider,
                "id": runtime.agent.model.id,
            },
            "running": runtime.turn is not None and not runtime.turn.done(),
            "warnings": list(runtime.warnings),
        }

    def _require_idle(self, runtime: SessionRuntime) -> None:
        if runtime.turn is not None and not runtime.turn.done():
            raise RpcError(BUSY, "a turn is running", {"sessionId": runtime.id})

    def _cwd_param(self, params: dict[str, Any]) -> Path:
        raw = optional_str(params, "cwd")
        base = self._default_cwd()
        if raw is None:
            return base
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = base / candidate
        candidate = candidate.resolve()
        if not candidate.is_dir():
            raise RpcError(INVALID_PARAMS, f"cwd is not a directory: {candidate}")
        return candidate

    def _default_cwd(self) -> Path:
        if self._default_id is not None:
            runtime = self._sessions.get(self._default_id)
            if runtime is not None:
                return runtime.options.cwd
        return Path.cwd()

    # -- events -----------------------------------------------------------
    async def _on_event(self, runtime: SessionRuntime, event: Any) -> None:
        if self._send is None:
            return
        self._seq += 1
        await self._send(
            notification(
                "event",
                {
                    "sessionId": runtime.id,
                    "turnId": runtime.turn_id,
                    "seq": self._seq,
                    "event": event.model_dump(mode="json"),
                },
            )
        )

    # -- methods ----------------------------------------------------------
    async def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        warnings = list(self._warnings)
        default = (
            self._sessions.get(self._default_id)
            if self._default_id is not None
            else None
        )
        if default is not None:
            warnings.extend(default.warnings)
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": {"name": self._name, "version": self._version},
            "capabilities": {
                "streaming": True,
                "approval": self._mode == "host",
                "sessions": True,
                "hostTools": False,
                "structuredChanges": True,
                "modelSelection": self._model_switcher is not None,
                "hostCredentials": "credentials/set" in self._extra_methods,
            },
            "mode": self._mode,
            "cwd": str(self._default_cwd()),
            "maxSessions": self._max_sessions,
            "accessModes": list(ACCESS_MODES),
            "session": (
                self._info(self._sessions[self._default_id])
                if self._default_id in self._sessions
                else None
            ),
            "warnings": warnings,
            "methods": list(METHODS) + sorted(self._extra_methods),
        }

    async def prompt(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime = self._resolve(params)
        content = require_str(params, "content")
        key = optional_str(params, "idempotencyKey")
        # A replay wins over a busy turn: a host that retried after a dropped
        # response must get its turn id back, not an error.
        if key is not None:
            record = runtime.idempotency_get(key)
            if record is not None:
                end = record.get("end")
                if (
                    isinstance(end, dict)
                    and record.get("status") != "running"
                    and self._send is not None
                ):
                    # Re-announce the outcome: the retrying client may not have
                    # seen the original notification.
                    await self._send(notification("turn/end", end))
                return {
                    "sessionId": runtime.id,
                    "turnId": record.get("turnId", ""),
                    "status": record.get("status"),
                    "replayed": True,
                }
        self._require_idle(runtime)
        turn_id = uuid.uuid4().hex[:12]
        runtime.turn_id = turn_id
        runtime.turn_started = time.time()
        runtime.end_future = asyncio.get_running_loop().create_future()
        if key is not None:
            # Record the mapping before the turn starts, so a host retry that
            # arrives while the turn is still running gets the same id back.
            runtime.idempotency_set(
                key, {"turnId": turn_id, "status": "running", "ts": time.time()}
            )
        runtime.turn = asyncio.ensure_future(
            self._run_turn(runtime, turn_id, content, key)
        )
        return {"sessionId": runtime.id, "turnId": turn_id, "status": "running"}

    async def cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime = self._resolve(params)
        turn_id = optional_str(params, "turnId")
        if runtime.turn is None or runtime.turn.done():
            raise RpcError(NO_TURN, "no turn is running")
        if turn_id is not None and turn_id != runtime.turn_id:
            raise RpcError(NO_TURN, f"turn {turn_id} is not running")
        runtime.agent.abort()
        return {}

    async def steer(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime = self._resolve(params)
        content = require_str(params, "content")
        if runtime.turn is None or runtime.turn.done():
            raise RpcError(NO_TURN, "no turn is running")
        runtime.agent.steer(content)
        return {}

    async def follow_up(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime = self._resolve(params)
        content = require_str(params, "content")
        if runtime.turn is None or runtime.turn.done():
            raise RpcError(NO_TURN, "no turn is running")
        runtime.agent.follow_up(content)
        return {}

    async def approve(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime = self._resolve(params)
        request_id = require_str(params, "requestId")
        decision = require_str(params, "decision")
        if decision not in DECISIONS:
            raise RpcError(
                INVALID_PARAMS, f"decision must be one of: {', '.join(DECISIONS)}"
            )
        remember = optional_bool(params, "remember")
        if not isinstance(runtime.approver, RpcApprover):
            raise RpcError(
                UNSUPPORTED, "approval bridge is not enabled (start with --mode host)"
            )
        if not runtime.approver.resolve(request_id, decision, remember):
            raise RpcError(
                INVALID_PARAMS,
                "unknown or already answered requestId",
                {"requestId": request_id},
            )
        return {"resolved": True}

    async def session_info(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._info(self._resolve(params))

    async def session_create(self, params: dict[str, Any]) -> dict[str, Any]:
        access = optional_str(params, "access") or "normal"
        if access not in ACCESS_MODES:
            raise RpcError(
                INVALID_PARAMS, f"access must be one of: {', '.join(ACCESS_MODES)}"
            )
        options = SessionOptions(
            cwd=self._cwd_param(params),
            access=access,
            tools=optional_str_list(params, "tools"),
            model=optional_str(params, "model"),
            instructions=optional_str(params, "instructions"),
        )
        return self._info(self._create(options))

    async def session_open(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._manager is None:
            raise RpcError(NO_SESSION, "sessions are disabled (--no-session)")
        raw = require_str(params, "path")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
        try:
            path.relative_to(self._manager.directory.resolve())
        except ValueError:
            raise RpcError(
                INVALID_PARAMS, "session path must be inside the sessions directory"
            ) from None
        for runtime in self._sessions.values():
            if runtime.session is not None and runtime.session.path.resolve() == path:
                return self._info(runtime)
        if not path.is_file():
            raise RpcError(INVALID_PARAMS, f"no such session file: {path}")
        session = self._manager.open(path)
        options = SessionOptions(
            cwd=Path(session.cwd).expanduser() if session.cwd else Path.cwd()
        )
        if not options.cwd.is_dir():
            options.cwd = Path.cwd()
        return self._info(self._create(options, session=session))

    async def session_list(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._manager is None:
            return {"sessions": []}
        cwd_filter = optional_str(params, "cwd")
        wanted: str | None = None
        if cwd_filter is not None:
            wanted = str(self._cwd_param({"cwd": cwd_filter}))
        entries: list[dict[str, Any]] = []
        for info in self._manager.list():
            if wanted is not None and info.cwd != wanted:
                continue
            entries.append(
                {
                    "sessionId": info.id,
                    "path": str(info.path),
                    "cwd": info.cwd,
                    "name": info.name,
                    "messageCount": info.message_count,
                    "updated": info.updated,
                    "open": info.id in self._sessions,
                }
            )
        return {"sessions": entries}

    async def session_fork(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime = self._resolve(params)
        if runtime.session is None or self._manager is None:
            raise RpcError(NO_SESSION, "fork needs a persisted session")
        self._require_idle(runtime)
        entry_id = optional_str(params, "entryId") or runtime.session.leaf_id
        if entry_id is None:
            raise RpcError(INVALID_PARAMS, "the session has no entry to fork from")
        cwd = Path(runtime.session.cwd or runtime.options.cwd)
        forked = self._manager.fork(runtime.session, entry_id, cwd=cwd)
        options = SessionOptions(
            cwd=cwd, access=runtime.options.access, tools=runtime.options.tools
        )
        return self._info(self._create(options, session=forked))

    async def session_compact(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime = self._resolve(params)
        self._require_idle(runtime)
        compacted = await runtime.agent.compact(
            instructions=optional_str(params, "instructions"),
            keep_recent=optional_int(params, "keepRecent"),
        )
        return {
            "compacted": bool(compacted),
            "messageCount": len(runtime.agent.messages),
        }

    async def session_undo(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime = self._resolve(params)
        count = optional_int(params, "count")
        if count is None:
            count = 1
        if count < 1:
            raise RpcError(INVALID_PARAMS, "count must be at least 1")
        if runtime.journal is None:
            raise RpcError(UNSUPPORTED, "no change journal is available")
        changes = [
            change
            for change in runtime.journal.last(count, session=runtime.id)
            if change.reversible and change.status == "applied"
        ]
        if not changes:
            return {"undone": []}
        host = detect_host()
        undone: list[str] = []
        for change in reversed(changes):
            undone.append(apply_undo(change, host))
            runtime.journal.mark(change.id, "reverted")
        return {"undone": undone}

    async def session_set_model(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime = self._resolve(params)
        self._require_idle(runtime)
        if self._model_switcher is None:
            raise RpcError(UNSUPPORTED, "model switching is not available")
        reference = require_str(params, "model")
        result = self._model_switcher(runtime, reference)
        runtime.options.model = reference
        runtime.warnings = list(result.get("warnings") or [])
        return {
            "sessionId": runtime.id,
            "model": result.get("model"),
            "warnings": runtime.warnings,
        }

    async def session_close(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime = self._resolve(params)
        if runtime.turn is not None and not runtime.turn.done():
            runtime.agent.abort()
            runtime.turn.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await runtime.turn
        if runtime.unsubscribe is not None:
            runtime.unsubscribe()
            runtime.unsubscribe = None
        if runtime.session is not None:
            with contextlib.suppress(Exception):
                runtime.session.storage.close()
        self._sessions.pop(runtime.id, None)
        if self._default_id == runtime.id:
            self._default_id = next(iter(self._sessions), None)
        return {"closed": runtime.id}

    async def session_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime = self._resolve(params)
        self._require_idle(runtime)
        session = runtime.session
        if session is None:
            raise RpcError(NO_SESSION, "this session is not persisted")
        path = Path(session.path)
        await self.session_close({"sessionId": runtime.id})
        with contextlib.suppress(OSError):
            path.unlink()
        return {"deleted": runtime.id, "path": str(path)}

    async def shutdown(self, params: dict[str, Any]) -> dict[str, Any]:
        # The server stops reading and exits after this response is written.
        if self._stop is not None:
            self._stop.set()
        return {}

    # -- turns ------------------------------------------------------------
    async def run_to_completion(
        self,
        content: str,
        *,
        session_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Start a turn and wait for its ``turn/end`` payload (MCP/ACP adapters)."""
        params: dict[str, Any] = {"content": content}
        if session_id is not None:
            params["sessionId"] = session_id
        if idempotency_key is not None:
            params["idempotencyKey"] = idempotency_key
        reply = await self.prompt(params)
        runtime = self._resolve({"sessionId": session_id})
        if reply.get("replayed"):
            record = (
                runtime.idempotency_get(idempotency_key)
                if idempotency_key is not None
                else None
            )
            if record is not None and isinstance(record.get("end"), dict):
                return dict(record["end"])
            return {
                "sessionId": runtime.id,
                "turnId": reply.get("turnId"),
                "status": record.get("status") if record else "replayed",
                "text": "",
                "error": None,
                "changedFiles": [],
            }
        future = runtime.end_future
        if future is None:
            raise RpcError(NO_TURN, "the turn did not start")
        return await future

    async def _run_turn(
        self,
        runtime: SessionRuntime,
        turn_id: str,
        content: str,
        key: str | None,
    ) -> None:
        status = "done"
        error: str | None = None
        try:
            await runtime.agent.prompt(content)
        except asyncio.CancelledError:
            payload = await self._emit_turn_end(runtime, turn_id, "aborted", None)
            self._remember(runtime, key, turn_id, payload)
            raise
        except Exception as exc:
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"
        else:
            reason = runtime.agent.last_stop_reason or "stop"
            if reason == "aborted":
                status = "aborted"
            elif reason == "error":
                status = "failed"
        payload = await self._emit_turn_end(runtime, turn_id, status, error)
        self._remember(runtime, key, turn_id, payload)

    @staticmethod
    def _remember(
        runtime: SessionRuntime,
        key: str | None,
        turn_id: str,
        payload: dict[str, Any],
    ) -> None:
        if key is None:
            return
        runtime.idempotency_set(
            key,
            {
                "turnId": turn_id,
                "status": payload.get("status"),
                "end": payload,
                "ts": time.time(),
            },
        )

    async def _emit_turn_end(
        self,
        runtime: SessionRuntime,
        turn_id: str,
        status: str,
        error: str | None,
    ) -> dict[str, Any]:
        messages = runtime.agent.messages
        usage: dict[str, Any] | None = None
        if runtime.session is not None:
            with contextlib.suppress(Exception):
                usage = runtime.session.usage_totals().model_dump(mode="json")
        changed: list[dict[str, Any]] = []
        with contextlib.suppress(Exception):
            # A memory session has no id in the journal; a single-session
            # process can still attribute every change to this turn.
            changed = collect_changes(
                runtime.journal,
                session_id=runtime.id,
                since_ts=runtime.turn_started,
                any_session=runtime.session is None,
            )
        payload: dict[str, Any] = {
            "sessionId": runtime.id,
            "turnId": turn_id,
            "status": status,
            "stopReason": runtime.agent.last_stop_reason,
            "text": _last_text(messages),
            "messageCount": len(messages),
            "usage": usage,
            "error": error,
            "changedFiles": changed,
        }
        if self._send is not None:
            await self._send(notification("turn/end", payload))
        if runtime.end_future is not None and not runtime.end_future.done():
            runtime.end_future.set_result(payload)
        return payload


__all__ = [
    "ACCESS_MODES",
    "BuiltSession",
    "METHODS",
    "ServerHost",
    "SessionBuilder",
    "SessionOptions",
    "SessionRuntime",
    "Wire",
]
