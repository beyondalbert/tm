"""The agent loop.

Port of pi's ``agent-loop.ts``: stream an assistant turn, execute any tool calls,
feed results back, and repeat until the model stops. Steering messages are
injected after a turn's tools finish; follow-up messages only after the agent
would otherwise stop.
"""

from __future__ import annotations

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

StreamFn = Callable[
    [Model, Context, StreamOptions],
    EventStream[AssistantMessageEvent, AssistantMessage],
]


@dataclass
class LoopHooks:
    emit: Callable[[AgentEvent], Awaitable[None]]
    execute_tools: Callable[[list[ToolCall]], Awaitable[list[ToolResultMessage]]]
    take_steering: Callable[[], list[Message]]
    take_follow_up: Callable[[], list[Message]]


_UPDATE_EVENTS = (TextDeltaEvent, ThinkingDeltaEvent, ToolCallDeltaEvent)


async def agent_loop(
    *,
    messages: list[Message],
    system_prompt: str | None,
    tools: list[ToolSpec],
    model: Model,
    stream_fn: StreamFn,
    options: StreamOptions,
    hooks: LoopHooks,
    max_turns: int = 100,
) -> list[Message]:
    history = list(messages)
    await hooks.emit(AgentStartEvent())
    turn = 0
    while turn < max_turns:
        turn += 1
        await hooks.emit(TurnStartEvent(turn=turn))

        context = Context(system_prompt=system_prompt, messages=history, tools=tools)
        stream = stream_fn(model, context, options)
        async for event in stream:
            if isinstance(event, StartEvent) and event.partial is not None:
                await hooks.emit(MessageStartEvent(message=event.partial))
            elif isinstance(event, _UPDATE_EVENTS) and event.partial is not None:
                await hooks.emit(MessageUpdateEvent(message=event.partial))

        assistant = await stream.result()
        history.append(assistant)
        await hooks.emit(MessageEndEvent(message=assistant))

        if assistant.stop_reason in ("error", "aborted"):
            break

        if assistant.tool_calls:
            results = await hooks.execute_tools(assistant.tool_calls)
            for result in results:
                history.append(result)
                await hooks.emit(MessageStartEvent(message=result))
                await hooks.emit(MessageEndEvent(message=result))

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

    await hooks.emit(AgentEndEvent(messages=history))
    return history


__all__ = ["LoopHooks", "StreamFn", "agent_loop"]
