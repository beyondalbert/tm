"""The agent loop.

Port of pi's ``agent-loop.ts``: stream an assistant turn, execute any tool calls,
feed results back, and repeat until the model stops. Steering messages are
injected after a turn's tools finish; follow-up messages only after the agent
would otherwise stop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from tm.ai.event_stream import EventStream
from tm.ai.providers.base import StreamOptions
from tm.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    Message,
    Model,
    StartEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCall,
    ToolCallDeltaEvent,
    ToolResultMessage,
    ToolSpec,
)
from tm.core.events import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from tm.telemetry import (
    SPAN_TURN,
    NoopTelemetry,
    Span,
    Telemetry,
    TelemetryContext,
)

StreamFn = Callable[
    [Model, Context, StreamOptions],
    EventStream[AssistantMessageEvent, AssistantMessage],
]


@dataclass
class LoopResult:
    messages: list[Message]
    #: "stop" (model finished), "max_turns", "error", or "aborted".
    stop_reason: str


@dataclass
class LoopHooks:
    emit: Callable[[AgentEvent], Awaitable[None]]
    execute_tools: Callable[[list[ToolCall]], Awaitable[list[ToolResultMessage]]]
    take_steering: Callable[[], list[Message]]
    take_follow_up: Callable[[], list[Message]]
    pause: Callable[[], Awaitable[None]]
    notice: Callable[[str, str], Awaitable[None]]


_UPDATE_EVENTS = (TextDeltaEvent, ThinkingDeltaEvent, ToolCallDeltaEvent)

#: Substrings that mark a provider error as transient (safe to retry).
_RETRYABLE_MARKERS = (
    "connection",
    "connect",
    "timeout",
    "timed out",
    "temporarily",
    "temporary",
    "rate limit",
    "429",
    "internal server error",
    "502",
    "503",
    "504",
    "overloaded",
    "unavailable",
    "reset by peer",
    "getaddrinfo",
    "network",
    "timedout",
    "ssl",
    "handshake",
    "broken pipe",
    "server error",
)


def _is_retryable_error(message: str | None) -> bool:
    if not message:
        return False
    text = message.lower()
    return any(marker in text for marker in _RETRYABLE_MARKERS)


def _retry_delay(attempt: int) -> float:
    return min(2.0**attempt, 30.0)


async def agent_loop(
    *,
    messages: list[Message],
    system_prompt: str | None,
    tools: list[ToolSpec],
    model: Model,
    stream_fn: StreamFn,
    options: StreamOptions,
    hooks: LoopHooks,
    max_turns: int = 300,
    max_retries: int = 3,
    telemetry: Telemetry | None = None,
    context: TelemetryContext | None = None,
) -> LoopResult:
    telemetry = telemetry or NoopTelemetry()
    context = context or TelemetryContext(trace_id="")
    history = list(messages)
    await hooks.emit(AgentStartEvent())
    turn = 0
    end_reason = "stop"
    while turn < max_turns:
        await hooks.pause()
        if options.signal is not None and options.signal.aborted:
            end_reason = "aborted"
            break
        turn += 1
        await hooks.emit(TurnStartEvent(turn=turn))
        turn_span = telemetry.start(Span(SPAN_TURN, {"turn": turn}), context)
        turn_status = "ok"

        attempt = 0
        while True:
            loop_context = Context(system_prompt=system_prompt, messages=history, tools=tools)
            stream = stream_fn(model, loop_context, options)
            async for event in stream:
                if isinstance(event, StartEvent) and event.partial is not None:
                    await hooks.emit(MessageStartEvent(message=event.partial))
                elif isinstance(event, _UPDATE_EVENTS) and event.partial is not None:
                    await hooks.emit(MessageUpdateEvent(message=event.partial))
            assistant = await stream.result()
            if (
                assistant.stop_reason == "error"
                and attempt < max_retries
                and _is_retryable_error(assistant.error_message)
            ):
                attempt += 1
                delay = _retry_delay(attempt)
                await hooks.notice(
                    f"network problem: retrying in {delay:g}s "
                    f"(attempt {attempt}/{max_retries})",
                    "warning",
                )
                await asyncio.sleep(delay)
                if options.signal is not None and options.signal.aborted:
                    assistant = AssistantMessage(
                        model=model.id, content=[], stop_reason="aborted"
                    )
                    break
                continue
            break

        history.append(assistant)
        await hooks.emit(MessageEndEvent(message=assistant))

        if assistant.stop_reason in ("error", "aborted"):
            turn_status = "error"
            end_reason = assistant.stop_reason
            telemetry.end(turn_span, context, status=turn_status)
            break

        if assistant.tool_calls:
            await hooks.pause()
            results = await hooks.execute_tools(assistant.tool_calls)
            for result in results:
                history.append(result)
                await hooks.emit(MessageStartEvent(message=result))
                await hooks.emit(MessageEndEvent(message=result))

        telemetry.end(turn_span, context, status=turn_status)
        await hooks.emit(TurnEndEvent(turn=turn))

        steering = hooks.take_steering()
        if steering:
            history.extend(steering)
            continue
        if assistant.tool_calls:
            continue

        follow_up = hooks.take_follow_up()
        if follow_up:
            history.extend(follow_up)
            continue
        break
    else:
        # The turn limit was reached while more work was pending.
        end_reason = "max_turns"

    await hooks.emit(AgentEndEvent(messages=history, stop_reason=end_reason))
    return LoopResult(history, end_reason)


__all__ = ["LoopHooks", "LoopResult", "StreamFn", "agent_loop"]
