"""High-level agent with state, message queues, and event subscription."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

from tm.ai.providers.base import Provider, StreamOptions
from tm.ai.types import (
    AssistantMessage,
    Context,
    Message,
    Model,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from tm.core.events import (
    AgentEndEvent,
    AgentEvent,
    MessageEndEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnStartEvent,
)
from tm.core.loop import LoopHooks, StreamFn, agent_loop
from tm.core.messages import repair_tool_messages
from tm.core.operation import Operation, OperationState, OperationStatus, PendingEffect
from tm.core.session import Session
from tm.core.store import Store
from tm.safety.journal import Journal
from tm.telemetry import (
    SPAN_OPERATION,
    SPAN_TOOL,
    NoopTelemetry,
    Span,
    Telemetry,
    TelemetryContext,
)
from tm.tools.base import Tool, ToolContext, ToolResult, text_result
from tm.utils.abort import AbortSignal

Listener = Callable[[AgentEvent], Awaitable[None]]


@dataclass
class BeforeToolCallResult:
    block: bool = False
    reason: str | None = None
    terminate: bool = False


BeforeToolCall = Callable[
    [ToolCall, "BaseModel"],
    Awaitable[BeforeToolCallResult | None] | BeforeToolCallResult | None,
]
AfterToolCall = Callable[
    [ToolCall, "BaseModel", ToolResult],
    Awaitable[ToolResult] | ToolResult,
]


async def _maybe_await(value):
    if asyncio.iscoroutine(value):
        return await value
    return value


def _normalize(content: str | Message | list[Message]) -> list[Message]:
    if isinstance(content, str):
        return [UserMessage(content=content)]
    if isinstance(content, list):
        return list(content)
    return [content]


@dataclass
class _Entry:
    call: ToolCall
    tool: Tool | None
    args: BaseModel | None
    error: str | None


class Agent:
    def __init__(
        self,
        model: Model,
        *,
        provider: Provider | None = None,
        tools: list[Tool] | None = None,
        system_prompt: str | None = None,
        stream_fn: StreamFn | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_turns: int = 100,
        cwd: Path | None = None,
        session: Session | None = None,
        auto_compact: bool = False,
        compact_threshold: float = 0.8,
        compact_keep_recent: int = 6,
        telemetry: Telemetry | None = None,
        store: Store | None = None,
        cache_retention: str | None = None,
        journal: Journal | None = None,
        dry_run: bool = False,
    ) -> None:
        self.model = model
        self.provider = provider
        self.tools: list[Tool] = list(tools or [])
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.cwd = cwd or Path.cwd()
        self.session = session
        self.auto_compact = auto_compact
        self.compact_threshold = compact_threshold
        self.compact_keep_recent = compact_keep_recent
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.before_tool_call: BeforeToolCall | None = None
        self.after_tool_call: AfterToolCall | None = None
        self.before_compact: Callable[[list[Message]], object] | None = None
        self.after_compact: Callable[[str], object] | None = None
        self.telemetry: Telemetry = telemetry or NoopTelemetry()
        self.store = store
        self.cache_retention = cache_retention
        self.journal = journal
        self.dry_run = dry_run
        self.operation: Operation | None = None
        self._trace_id = ""
        self._operation_span_id: str | None = None

        if stream_fn is None and provider is None:
            raise ValueError("Agent requires either a provider or a stream_fn")
        self._stream_fn: StreamFn = stream_fn or self._provider_stream

        self._messages: list[Message] = session.messages() if session is not None else []
        self._listeners: list[Listener] = []
        self._steering: list[Message] = []
        self._follow_up: list[Message] = []
        self._pending_messages: list[Message] = []
        self._signal: AbortSignal | None = None

    def _storage(self) -> Store | None:
        """The store for operation state: the session's storage when there is one."""
        if self.session is not None:
            return self.session.storage
        return self.store

    # -- state ------------------------------------------------------------
    @property
    def messages(self) -> list[Message]:
        return list(self._messages)

    def resume(self, session: Session) -> None:
        """Load a persisted session and continue from its active branch."""
        self.session = session
        self._messages = session.messages()

    def reset(self) -> None:
        """Clear the in-memory conversation, including queued messages."""
        self._messages = []
        self._steering = []
        self._follow_up = []

    def set_messages(self, messages: list[Message]) -> None:
        self._messages = list(messages)

    async def compact(self, instructions: str | None = None, keep_recent: int = 6) -> bool:
        """Summarize older messages. Persists a compaction entry when possible."""
        from tm.core.compaction import summarize_messages

        if len(self._messages) <= keep_recent:
            return False
        index = len(self._messages) - keep_recent
        # Never start the retained tail with a tool result: include the assistant
        # that requested it, so the provider sees a valid tool call sequence.
        while index > 0 and isinstance(self._messages[index], ToolResultMessage):
            index -= 1
        older = self._messages[:index]
        recent = repair_tool_messages(self._messages[index:])
        if not older:
            return False
        if self.before_compact is not None:
            await _maybe_await(self.before_compact(older))
        summary = await summarize_messages(self._stream_fn, self.model, older, instructions)
        if not summary:
            return False
        if self.session is not None:
            # Durable: the summary and copied-forward tail survive a restart.
            self.session.append_compaction(summary, recent)
            self._messages = self.session.messages()
        else:
            self._messages = [
                UserMessage(content=f"Summary of earlier conversation:\n{summary}"),
                *recent,
            ]
        if self.after_compact is not None:
            await _maybe_await(self.after_compact(summary))
        return True

    def _context_overflowed(self) -> bool:
        for message in reversed(self._messages):
            if not isinstance(message, AssistantMessage):
                continue
            if message.stop_reason == "length":
                return True
            if message.stop_reason == "error" and message.error_message:
                text = message.error_message.lower()
                return any(
                    token in text
                    for token in ("context", "too long", "maximum", "token", "length")
                )
            return False
        return False

    async def _maybe_auto_compact(self) -> bool:
        from tm.core.compaction import estimate_tokens

        if len(self._messages) <= self.compact_keep_recent:
            return False
        estimated = estimate_tokens(self._messages, self.system_prompt)
        limit = int(self.model.context_window * self.compact_threshold)
        if estimated < limit:
            return False
        return await self.compact(keep_recent=self.compact_keep_recent)

    @property
    def is_running(self) -> bool:
        return self._signal is not None and not self._signal.aborted and self._running

    _running: bool = False

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    async def _emit(self, event: AgentEvent) -> None:
        if self.operation is not None and isinstance(event, TurnStartEvent):
            self.operation.set_turn(event.turn)
        if isinstance(event, MessageEndEvent):
            self._persist_message(event.message)
        if isinstance(event, AgentEndEvent):
            self._flush_messages()
        for listener in list(self._listeners):
            await listener(event)

    def _persist_message(self, message: Message) -> None:
        self._pending_messages.append(message)

    def _flush_messages(self) -> None:
        """Commit the accumulated turn messages (and usage) atomically."""
        if self.session is None or not self._pending_messages:
            return
        self.session.append_messages(self._pending_messages)
        self._pending_messages = []

    # -- queueing ---------------------------------------------------------
    def steer(self, content: str | Message) -> None:
        self._steering.extend(_normalize(content))

    def follow_up(self, content: str | Message) -> None:
        self._follow_up.extend(_normalize(content))

    def _take_steering(self) -> list[Message]:
        queued, self._steering = self._steering, []
        return queued

    def _take_follow_up(self) -> list[Message]:
        queued, self._follow_up = self._follow_up, []
        return queued

    def abort(self) -> None:
        if self._signal is not None:
            self._signal.abort()

    # -- run --------------------------------------------------------------
    async def prompt(self, content: str | Message | list[Message]) -> list[Message]:
        new_messages = _normalize(content)
        if self.auto_compact:
            await self._maybe_auto_compact()
        self._messages.extend(new_messages)
        if self.session is not None:
            # Persist the user turn before the request so it is durable.
            self.session.append_messages(new_messages)
        return await self._run_loop(new_messages)

    async def _run_loop(self, new_messages: list[Message]) -> list[Message]:
        self._signal = AbortSignal()
        self._running = True
        options = StreamOptions(
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            signal=self._signal,
            cache_retention=self.cache_retention,
        )
        hooks = LoopHooks(
            emit=self._emit,
            execute_tools=self._execute_tools,
            take_steering=self._take_steering,
            take_follow_up=self._take_follow_up,
        )
        context = TelemetryContext(trace_id=uuid.uuid4().hex)
        span_id = self.telemetry.start(
            Span(
                SPAN_OPERATION,
                {
                    "model": f"{self.model.provider}/{self.model.id}",
                    "new_entries": len(new_messages),
                },
            ),
            context,
        )
        self._trace_id = context.trace_id
        self._operation_span_id = span_id or None
        child_context = TelemetryContext(
            trace_id=context.trace_id, parent_id=self._operation_span_id
        )
        session_id = self.session.id if self.session is not None else None
        storage = self._storage()
        operation = (
            Operation.accept(
                uuid.uuid4().hex[:12], storage, kind="run", session_id=session_id
            )
            if storage is not None
            else None
        )
        self.operation = operation
        status = "ok"
        recovery_attempts = 0
        try:
            while True:
                self._messages = await agent_loop(
                    messages=repair_tool_messages(self._messages),
                    system_prompt=self.system_prompt,
                    tools=[tool.spec() for tool in self.tools],
                    model=self.model,
                    stream_fn=self._stream_fn,
                    options=options,
                    hooks=hooks,
                    max_turns=self.max_turns,
                    telemetry=self.telemetry,
                    context=child_context,
                )
                if (
                    recovery_attempts == 0
                    and self._context_overflowed()
                    and len(self._messages) > self.compact_keep_recent
                ):
                    # Context overflowed: persist what we have, compact, retry once.
                    self._flush_messages()
                    recovery_attempts += 1
                    if await self.compact(keep_recent=self.compact_keep_recent):
                        continue
                break
        except BaseException:
            status = "error"
            raise
        finally:
            self._running = False
            self._flush_messages()
            if status == "ok":
                status = self._final_run_status()
            self.telemetry.end(span_id, context, status=status)
            if operation is not None:
                operation.settle(
                    self._terminal_status(status), message_count=len(self._messages)
                )
        return new_messages

    # -- recovery ---------------------------------------------------------
    def pending_recovery(self) -> list[OperationState]:
        """Interrupted operations for this session that recovery would reconcile."""
        storage = self._storage()
        if storage is None:
            return []
        session_id = self.session.id if self.session is not None else None
        return Operation.unsettled(storage, session_id)

    async def recover(self) -> bool:
        """Reconcile interrupted operations and continue the run.

        A replay-safe effect is re-run from the persisted intent. A non-replay-safe
        effect is surfaced as an error result without re-running, because it may or
        may not have happened. Returns True if any interrupted operation was found.
        """
        storage = self._storage()
        if storage is None:
            return False
        states = self.pending_recovery()
        if not states:
            return False
        resume = False
        for state in states:
            operation = Operation(state.operation_id, storage)
            if state.status is OperationStatus.EFFECT_PENDING and state.pending is not None:
                recovered = await self._recover_effect(state)
                if recovered is not None:
                    message, effect_status = recovered
                    self._messages.append(message)
                    self._persist_message(message)
                    resume = True
                    operation.settle(effect_status, message_count=len(self._messages))
                    continue
                # No message produced: either the result was already recorded
                # (settle completed) or the effect cannot be reconciled.
                pending = state.pending
                already = any(
                    isinstance(message, ToolResultMessage)
                    and message.tool_call_id == pending.call_id
                    for message in self._messages
                )
                operation.settle(
                    OperationStatus.COMPLETED if already else OperationStatus.FAILED,
                    message_count=len(self._messages),
                )
            else:
                operation.settle(OperationStatus.ABORTED, message_count=len(self._messages))
        if resume:
            await self._run_loop([])
        return True

    async def _recover_effect(
        self, state: OperationState
    ) -> tuple[ToolResultMessage, OperationStatus] | None:
        pending = state.pending
        assert pending is not None
        for message in self._messages:
            if (
                isinstance(message, ToolResultMessage)
                and message.tool_call_id == pending.call_id
            ):
                return None  # already settled
        tool = next((tool for tool in self.tools if tool.name == pending.tool_name), None)
        status = OperationStatus.COMPLETED
        if tool is None:
            result = text_result(
                f"Recovered: tool '{pending.tool_name}' is no longer available.",
                is_error=True,
            )
            status = OperationStatus.FAILED
        elif tool.replay_safe:
            call = ToolCall(
                id=pending.call_id,
                name=pending.tool_name,
                arguments=pending.arguments,
            )
            try:
                args = tool.parameters_model.model_validate(pending.arguments)
            except ValidationError as exc:
                result = text_result(
                    f"Recovered re-run skipped: invalid arguments ({exc})", is_error=True
                )
                status = OperationStatus.FAILED
            else:
                blocked = None
                if self.before_tool_call is not None:
                    blocked = await _maybe_await(self.before_tool_call(call, args))
                if blocked is not None and blocked.block:
                    result = text_result(blocked.reason or "Blocked", is_error=True)
                    status = OperationStatus.FAILED
                else:
                    try:
                        result = await tool.execute(
                            pending.call_id,
                            args,
                            ToolContext(
                                cwd=self.cwd,
                                session=self.session.id if self.session is not None else None,
                                journal=self.journal,
                                dry_run=self.dry_run,
                            ),
                        )
                    except Exception as exc:  # noqa: BLE001 - failures become results
                        result = text_result(
                            f"Recovered re-run failed: {exc}", is_error=True
                        )
                        status = OperationStatus.FAILED
        else:
            result = text_result(
                f"Recovered after an interruption: '{pending.tool_name}' was in flight "
                "when the process stopped and is not replay-safe, so it was not re-run. "
                "Check its effects before continuing.",
                is_error=True,
            )
            status = OperationStatus.FAILED
        message = ToolResultMessage(
            tool_call_id=pending.call_id,
            tool_name=pending.tool_name,
            content=result.content or [TextContent(text="")],
            is_error=result.is_error,
        )
        return message, status

    def _final_run_status(self) -> str:
        if self._signal is not None and self._signal.aborted:
            return "aborted"
        for message in reversed(self._messages):
            if isinstance(message, AssistantMessage):
                if message.stop_reason == "error":
                    return "error"
                if message.stop_reason == "aborted":
                    return "aborted"
                return "ok"
        return "ok"

    def _terminal_status(self, status: str) -> OperationStatus:
        if status == "error":
            return OperationStatus.FAILED
        if status == "aborted":
            return OperationStatus.ABORTED
        return OperationStatus.COMPLETED

    def _provider_stream(self, model: Model, context: Context, options: StreamOptions):
        assert self.provider is not None
        return self.provider.stream(model, context, options)

    # -- tool execution ---------------------------------------------------
    async def _execute_tools(self, calls: list[ToolCall]) -> list[ToolResultMessage]:
        tools_by_name = {tool.name: tool for tool in self.tools}
        entries: list[_Entry] = []
        for call in calls:
            tool = tools_by_name.get(call.name)
            if tool is None:
                entries.append(_Entry(call, None, None, f"Unknown tool: {call.name}"))
                continue
            try:
                args = tool.parameters_model.model_validate(call.arguments)
            except ValidationError as exc:
                entries.append(_Entry(call, tool, None, f"Invalid arguments: {exc}"))
                continue
            entries.append(_Entry(call, tool, args, None))

        sequential = any(
            entry.tool is not None and entry.tool.execution_mode == "sequential"
            for entry in entries
        )
        if sequential:
            return [await self._exec_entry(entry) for entry in entries]
        return list(await asyncio.gather(*(self._exec_entry(e) for e in entries)))

    async def _exec_entry(self, entry: _Entry) -> ToolResultMessage:
        call = entry.call
        context = TelemetryContext(
            trace_id=self._trace_id or uuid.uuid4().hex,
            parent_id=self._operation_span_id,
        )
        span_id = self.telemetry.start(
            Span(SPAN_TOOL, {"tool": call.name}),
            context,
        )
        span_status = "ok"
        try:
            return await self._exec_entry_inner(entry)
        except BaseException:
            span_status = "error"
            raise
        finally:
            self.telemetry.end(span_id, context, status=span_status)

    async def _exec_entry_inner(self, entry: _Entry) -> ToolResultMessage:
        call = entry.call
        if entry.error is not None or entry.tool is None or entry.args is None:
            result = text_result(entry.error or "Tool unavailable", is_error=True)
            await self._emit(
                ToolExecutionEndEvent(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    is_error=True,
                    output=result.text(),
                )
            )
            return self._result_message(call, result)

        if self.before_tool_call is not None:
            decision = await _maybe_await(self.before_tool_call(call, entry.args))
            if decision is not None and decision.block:
                result = text_result(decision.reason or "Blocked", is_error=True)
                return self._result_message(call, result)

        async def on_update(text: str) -> None:
            await self._emit(
                ToolExecutionUpdateEvent(
                    tool_call_id=call.id, tool_name=call.name, text=text
                )
            )

        await self._emit(
            ToolExecutionStartEvent(
                tool_call_id=call.id, tool_name=call.name, arguments=call.arguments
            )
        )
        tool_context = ToolContext(
            cwd=self.cwd,
            signal=self._signal,
            on_update=on_update,
            session=self.session.id if self.session is not None else None,
            journal=self.journal,
            dry_run=self.dry_run,
        )
        operation = self.operation
        if operation is not None:
            # Intent before the uncertain effect: if the process dies here, the
            # durable state says a tool may or may not have run.
            operation.begin_effect(
                PendingEffect(
                    tool_name=call.name,
                    call_id=call.id,
                    arguments=call.arguments,
                    replay_safe=entry.tool.replay_safe,
                ),
                turn=operation.state.turn,
            )
        try:
            result = await entry.tool.execute(call.id, entry.args, tool_context)
        except Exception as exc:  # noqa: BLE001 - tool failures become error results
            result = text_result(f"{type(exc).__name__}: {exc}", is_error=True)
        finally:
            if operation is not None:
                operation.settle_effect(message_count=len(self._messages))

        if self.after_tool_call is not None:
            result = await _maybe_await(self.after_tool_call(call, entry.args, result))

        await self._emit(
            ToolExecutionEndEvent(
                tool_call_id=call.id,
                tool_name=call.name,
                is_error=result.is_error,
                output=result.text(),
            )
        )
        return self._result_message(call, result)

    @staticmethod
    def _result_message(call: ToolCall, result: ToolResult) -> ToolResultMessage:
        return ToolResultMessage(
            tool_call_id=call.id,
            tool_name=call.name,
            content=result.content or [TextContent(text="")],
            is_error=result.is_error,
        )


__all__ = ["AfterToolCall", "Agent", "BeforeToolCall", "BeforeToolCallResult"]
