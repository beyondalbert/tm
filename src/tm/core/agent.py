"""High-level agent with state, message queues, and event subscription."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

from tm.ai.providers.base import Provider, StreamOptions
from tm.ai.types import (
    Context,
    Message,
    Model,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from tm.core.events import (
    AgentEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from tm.core.loop import LoopHooks, StreamFn, agent_loop
from tm.core.session import Session
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

        if stream_fn is None and provider is None:
            raise ValueError("Agent requires either a provider or a stream_fn")
        self._stream_fn: StreamFn = stream_fn or self._provider_stream

        self._messages: list[Message] = session.messages() if session is not None else []
        self._listeners: list[Listener] = []
        self._steering: list[Message] = []
        self._follow_up: list[Message] = []
        self._signal: AbortSignal | None = None

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
        """Summarize older messages in place. Returns True if it compacted."""
        from tm.core.compaction import summarize_messages

        if len(self._messages) <= keep_recent:
            return False
        older = self._messages[:-keep_recent]
        recent = self._messages[-keep_recent:]
        summary = await summarize_messages(self._stream_fn, self.model, older, instructions)
        if not summary:
            return False
        self._messages = [
            UserMessage(content=f"Summary of earlier conversation:\n{summary}"),
            *recent,
        ]
        return True

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
        for listener in list(self._listeners):
            await listener(event)

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
        persist_from = len(self._messages) - len(new_messages)
        self._signal = AbortSignal()
        self._running = True
        options = StreamOptions(
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            signal=self._signal,
        )
        hooks = LoopHooks(
            emit=self._emit,
            execute_tools=self._execute_tools,
            take_steering=self._take_steering,
            take_follow_up=self._take_follow_up,
        )
        try:
            self._messages = await agent_loop(
                messages=self._messages,
                system_prompt=self.system_prompt,
                tools=[tool.spec() for tool in self.tools],
                model=self.model,
                stream_fn=self._stream_fn,
                options=options,
                hooks=hooks,
                max_turns=self.max_turns,
            )
        finally:
            self._running = False
        if self.session is not None:
            for message in self._messages[persist_from:]:
                self.session.append(message)
        return new_messages

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
        context = ToolContext(cwd=self.cwd, signal=self._signal, on_update=on_update)
        try:
            result = await entry.tool.execute(call.id, entry.args, context)
        except Exception as exc:  # noqa: BLE001 - tool failures become error results
            result = text_result(f"{type(exc).__name__}: {exc}", is_error=True)

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
