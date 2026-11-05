"""The agent loop.

Port of pi's ``agent-loop.ts``: stream an assistant turn, execute any tool calls,
feed results back, and repeat until the model stops. Steering messages are
injected after a turn's tools finish; follow-up messages only after the agent
would otherwise stop.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import deque
from dataclasses import dataclass, replace
from typing import Awaitable, Callable

from tm.ai.event_stream import EventStream
from tm.ai.providers.base import StreamOptions
from tm.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    Message,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCall,
    ToolCallDeltaEvent,
    ToolResultMessage,
    ToolSpec,
    UserMessage,
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
from tm.i18n import t as _t
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
    #: Whether a length stop means the prompt overflowed the context window.
    #: Such stops are left to the caller to compact instead of continuing.
    is_context_overflow: Callable[[AssistantMessage], bool] | None = None
    #: Before every request: returns (canonical history, request messages).
    #: The canonical history may change (compaction); the request view may be
    #: trimmed (old tool output rewritten) without touching the session.
    prepare: (
        Callable[[list[Message]], Awaitable[tuple[list[Message], list[Message]]]]
        | None
    ) = None


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


def _is_tool_choice_error(message: str | None) -> bool:
    if not message:
        return False
    text = message.lower()
    return "tool_choice" in text or "tool choice" in text


#: Fed back to the model when an output-limit stop truncated a tool call.
_TRUNCATED_TOOL_RESULT = (
    "The tool call was cut off by the output token limit, so it was not executed. "
    "Reissue the call with complete arguments, or split it into smaller calls."
)

#: Injected after a reply is cut off by the model's output token limit.
_CONTINUATION_PROMPT = (
    "Your previous reply hit the output token limit and was cut off. "
    "Continue from exactly where it stopped; do not repeat work that is already done."
)
#: How much of the cut-off reasoning/reply to carry into the continuation.
_CONTINUATION_TAIL_CHARS = 3000


def _tail(text: str) -> str:
    if len(text) <= _CONTINUATION_TAIL_CHARS:
        return text
    return "..." + text[-_CONTINUATION_TAIL_CHARS:]


def _continuation_message(assistant: AssistantMessage) -> UserMessage:
    """Give the model its own cut-off plan back so it can resume, not restart.

    Reasoning content is not sent back to OpenAI-compatible APIs, so without
    this the model re-derives the whole plan on every continuation and often
    hits the same output limit again.
    """
    sections = [_CONTINUATION_PROMPT]
    thinking = assistant.thinking().strip()
    text = assistant.text().strip()
    if thinking:
        sections.append("Where your reasoning stopped:\n" + _tail(thinking))
    if text:
        sections.append("Where your reply stopped:\n" + _tail(text))
    sections.append("Take the next concrete step; do not restart the analysis.")
    return UserMessage(content="\n\n".join(sections))


#: Raw tool-call markup leaking into assistant text means the server's
#: tool-call parser did not recognize the model's format.
_RAW_TOOL_MARKERS = (
    "</parameter>",
    "<tool_call>",
    "</tool_call>",
    "<|tool_call",
    "<tool_calls_begin|>",
    "<|tool_calls_begin|>",
    "<function_call>",
    "</invoke>",
)


def _has_raw_tool_markup(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _RAW_TOOL_MARKERS)


def _is_empty_reply(message: AssistantMessage) -> bool:
    """A finished turn with no text and no tool calls (reasoning may exist)."""
    if message.stop_reason != "stop" or message.tool_calls:
        return False
    if message.text().strip():
        return False
    if message.thinking().strip():
        return True
    usage = message.usage
    return usage is not None and usage.output > 0


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
    max_length_continues: int = 3,
    max_length_stalls: int = 2,
    max_repeated_tool_calls: int = 3,
    repeated_call_window: int = 40,
    telemetry: Telemetry | None = None,
    context: TelemetryContext | None = None,
) -> LoopResult:
    telemetry = telemetry or NoopTelemetry()
    context = context or TelemetryContext(trace_id="")
    history = list(messages)
    await hooks.emit(AgentStartEvent())
    turn = 0
    length_continues = 0
    length_stalls = 0
    force_tool_choice = False
    recent_tool_calls: deque[str] = deque(maxlen=max(1, repeated_call_window))
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
        request_messages = history
        if hooks.prepare is not None:
            history, request_messages = await hooks.prepare(history)

        empty_reply = False
        attempt = 0
        while True:
            forced = force_tool_choice and bool(tools)
            if forced:
                # One-shot: the next request must produce a tool call instead
                # of restarting a long planning pass.
                force_tool_choice = False
            request_options = (
                replace(options, tool_choice="required") if forced else options
            )
            loop_context = Context(
                system_prompt=system_prompt, messages=request_messages, tools=tools
            )
            stream = stream_fn(model, loop_context, request_options)
            watcher: asyncio.Task[None] | None = None
            if options.signal is not None:
                abort_signal = options.signal

                async def _abort_stream(stream=stream, signal=abort_signal) -> None:
                    await signal.wait()
                    # Cancel the in-flight request and finish the stream so a
                    # stalled or slow response stops immediately.
                    stream.cancel()
                    stream.end(
                        AssistantMessage(model=model.id, content=[], stop_reason="aborted")
                    )

                watcher = asyncio.create_task(_abort_stream())
            try:
                async for event in stream:
                    if isinstance(event, StartEvent) and event.partial is not None:
                        await hooks.emit(MessageStartEvent(message=event.partial))
                    elif isinstance(event, _UPDATE_EVENTS) and event.partial is not None:
                        await hooks.emit(MessageUpdateEvent(message=event.partial))
                assistant = await stream.result()
            finally:
                if watcher is not None:
                    watcher.cancel()
            if (
                forced
                and assistant.stop_reason == "error"
                and _is_tool_choice_error(assistant.error_message)
            ):
                # Some OpenAI-compatible servers reject tool_choice="required".
                # Retry the same turn with the normal default.
                continue
            if (
                assistant.stop_reason == "error"
                and attempt < max_retries
                and _is_retryable_error(assistant.error_message)
            ):
                attempt += 1
                delay = _retry_delay(attempt)
                await hooks.notice(
                    _t("notice.network_retry", delay=f"{delay:g}", n=attempt, max=max_retries),
                    "warning",
                )
                await asyncio.sleep(delay)
                if options.signal is not None and options.signal.aborted:
                    assistant = AssistantMessage(
                        model=model.id, content=[], stop_reason="aborted"
                    )
                    break
                continue
            if _is_empty_reply(assistant):
                if attempt < max_retries:
                    attempt += 1
                    delay = _retry_delay(attempt)
                    await hooks.notice(
                        _t(
                            "notice.empty_retry",
                            delay=f"{delay:g}",
                            n=attempt,
                            max=max_retries,
                        ),
                        "warning",
                    )
                    await asyncio.sleep(delay)
                    if options.signal is not None and options.signal.aborted:
                        assistant = AssistantMessage(
                            model=model.id, content=[], stop_reason="aborted"
                        )
                        break
                    continue
                empty_reply = True
            break

        history.append(assistant)
        await hooks.emit(MessageEndEvent(message=assistant))

        if empty_reply:
            end_reason = "empty"
            telemetry.end(turn_span, context, status="error")
            break
        if assistant.stop_reason in ("error", "aborted"):
            turn_status = "error"
            end_reason = assistant.stop_reason
            telemetry.end(turn_span, context, status=turn_status)
            break

        raw_tool_markup = _has_raw_tool_markup(assistant.text())
        looped_tool: str | None = None
        if assistant.tool_calls:
            if assistant.stop_reason == "length":
                # The JSON arguments may be truncated; executing them would run
                # the tool with missing data. Report the truncation instead.
                results = [
                    ToolResultMessage(
                        tool_call_id=call.id,
                        tool_name=call.name,
                        content=[TextContent(text=_TRUNCATED_TOOL_RESULT)],
                        is_error=True,
                    )
                    for call in assistant.tool_calls
                ]
            else:
                await hooks.pause()
                results = await hooks.execute_tools(assistant.tool_calls)
            for call, result in zip(assistant.tool_calls, results):
                history.append(result)
                await hooks.emit(MessageStartEvent(message=result))
                await hooks.emit(MessageEndEvent(message=result))
                if max_repeated_tool_calls > 0:
                    signature = call.name + "|" + json.dumps(
                        call.arguments, sort_keys=True, ensure_ascii=False
                    )
                    digest = hashlib.sha1(
                        result.text().encode("utf-8", errors="replace")
                    ).hexdigest()
                    key = signature + "|" + digest
                    recent_tool_calls.append(key)
                    if recent_tool_calls.count(key) >= max_repeated_tool_calls:
                        looped_tool = call.name
                        break

        telemetry.end(turn_span, context, status=turn_status)
        await hooks.emit(TurnEndEvent(turn=turn))

        if raw_tool_markup:
            await hooks.notice(_t("notice.tool_markup"), "error")
            end_reason = "tool_parser"
            break

        if looped_tool is not None:
            await hooks.notice(
                _t(
                    "notice.tool_loop",
                    name=looped_tool,
                    n=max_repeated_tool_calls,
                ),
                "warning",
            )
            end_reason = "loop"
            break

        if assistant.stop_reason == "length":
            # The output budget was exhausted, not the work. Continue from the
            # truncated turn unless the prompt itself overflowed the window.
            progress = bool(assistant.tool_calls) or bool(assistant.text().strip())
            length_stalls = 0 if progress else length_stalls + 1
            stalled = max_length_stalls > 0 and length_stalls >= max_length_stalls
            overflow = (
                hooks.is_context_overflow is not None
                and hooks.is_context_overflow(assistant)
            )
            if (
                not overflow
                and not stalled
                and length_continues < max_length_continues
            ):
                length_continues += 1
                await hooks.notice(
                    _t(
                        "notice.length_continue",
                        n=length_continues,
                        max=max_length_continues,
                    ),
                    "warning",
                )
                continuation = _continuation_message(assistant)
                history.append(continuation)
                await hooks.emit(MessageStartEvent(message=continuation))
                await hooks.emit(MessageEndEvent(message=continuation))
                if length_continues == 1 and tools and not assistant.text().strip():
                    force_tool_choice = True
                continue
            end_reason = "length"
            break
        length_continues = 0
        length_stalls = 0

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
