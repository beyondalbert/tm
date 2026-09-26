"""Agent runtime events, consumed by UIs and extensions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from tm.ai.types import AssistantMessage, Message


class AgentEvent(BaseModel):
    type: str


class AgentStartEvent(AgentEvent):
    type: Literal["agent_start"] = "agent_start"


class TurnStartEvent(AgentEvent):
    type: Literal["turn_start"] = "turn_start"
    turn: int


class MessageStartEvent(AgentEvent):
    type: Literal["message_start"] = "message_start"
    message: Message


class MessageUpdateEvent(AgentEvent):
    type: Literal["message_update"] = "message_update"
    message: AssistantMessage


class MessageEndEvent(AgentEvent):
    type: Literal["message_end"] = "message_end"
    message: Message


class ToolExecutionStartEvent(AgentEvent):
    type: Literal["tool_execution_start"] = "tool_execution_start"
    tool_call_id: str
    tool_name: str
    arguments: dict


class ToolExecutionUpdateEvent(AgentEvent):
    type: Literal["tool_execution_update"] = "tool_execution_update"
    tool_call_id: str
    tool_name: str
    text: str


class ToolExecutionEndEvent(AgentEvent):
    type: Literal["tool_execution_end"] = "tool_execution_end"
    tool_call_id: str
    tool_name: str
    is_error: bool
    output: str


class TurnEndEvent(AgentEvent):
    type: Literal["turn_end"] = "turn_end"
    turn: int


class AgentEndEvent(AgentEvent):
    type: Literal["agent_end"] = "agent_end"
    messages: list[Message]
    #: Why the run ended: "stop", "max_turns", "error", or "aborted".
    stop_reason: str | None = None


class AgentNoticeEvent(AgentEvent):
    """A user-facing notice from the runtime (e.g. a retry)."""

    type: Literal["agent_notice"] = "agent_notice"
    text: str
    #: "info", "warning", or "error".
    level: str = "info"


__all__ = [
    "AgentEndEvent",
    "AgentEvent",
    "AgentNoticeEvent",
    "AgentStartEvent",
    "MessageEndEvent",
    "MessageStartEvent",
    "MessageUpdateEvent",
    "ToolExecutionEndEvent",
    "ToolExecutionStartEvent",
    "ToolExecutionUpdateEvent",
    "TurnEndEvent",
    "TurnStartEvent",
]
