"""Provider-neutral LLM data model.

Mirrors the role of ``pi-ai``'s types: messages, tools, models, usage, and the
streaming event union. Providers translate these to/from their wire formats.
"""

from __future__ import annotations

import time
from typing import Annotated, Literal

from pydantic import BaseModel, Field

StopReason = Literal["stop", "length", "tool_use", "error", "aborted"]


def now_ms() -> int:
    return int(time.time() * 1000)


class Usage(BaseModel):
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total: int = 0


class TextContent(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ThinkingContent(BaseModel):
    type: Literal["thinking"] = "thinking"
    thinking: str


class ImageContent(BaseModel):
    type: Literal["image"] = "image"
    data: str
    mime_type: str


Content = Annotated[
    TextContent | ThinkingContent | ImageContent,
    Field(discriminator="type"),
]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict = Field(default_factory=dict)


class UserMessage(BaseModel):
    role: Literal["user"] = "user"
    content: str | list[Content]
    timestamp: int = Field(default_factory=now_ms)


class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: list[Content] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: StopReason | None = None
    error_message: str | None = None
    usage: Usage | None = None
    model: str | None = None
    timestamp: int = Field(default_factory=now_ms)

    def text(self) -> str:
        return "".join(c.text for c in self.content if isinstance(c, TextContent))

    def thinking(self) -> str:
        return "".join(c.thinking for c in self.content if isinstance(c, ThinkingContent))


class ToolResultMessage(BaseModel):
    role: Literal["tool"] = "tool"
    tool_call_id: str
    tool_name: str
    content: list[Content]
    is_error: bool = False
    timestamp: int = Field(default_factory=now_ms)

    def text(self) -> str:
        return "".join(c.text for c in self.content if isinstance(c, TextContent))


Message = Annotated[
    UserMessage | AssistantMessage | ToolResultMessage,
    Field(discriminator="role"),
]


class ToolSpec(BaseModel):
    """Tool definition handed to the LLM (JSON Schema parameters)."""

    name: str
    description: str
    parameters: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})


class Model(BaseModel):
    id: str
    provider: str
    api: str = "openai-completions"
    base_url: str | None = None
    context_window: int = 128_000
    max_tokens: int = 8_192
    reasoning: bool = False


class Context(BaseModel):
    system_prompt: str | None = None
    messages: list[Message] = Field(default_factory=list)
    tools: list[ToolSpec] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Streaming events
# ---------------------------------------------------------------------------


class AssistantMessageEvent(BaseModel):
    type: str
    partial: AssistantMessage | None = None


class StartEvent(AssistantMessageEvent):
    type: Literal["start"] = "start"


class TextDeltaEvent(AssistantMessageEvent):
    type: Literal["text_delta"] = "text_delta"
    content_index: int = 0
    delta: str = ""


class ThinkingDeltaEvent(AssistantMessageEvent):
    type: Literal["thinking_delta"] = "thinking_delta"
    content_index: int = 0
    delta: str = ""


class ToolCallStartEvent(AssistantMessageEvent):
    type: Literal["toolcall_start"] = "toolcall_start"
    content_index: int = 0


class ToolCallDeltaEvent(AssistantMessageEvent):
    type: Literal["toolcall_delta"] = "toolcall_delta"
    content_index: int = 0
    delta: str = ""


class ToolCallEndEvent(AssistantMessageEvent):
    type: Literal["toolcall_end"] = "toolcall_end"
    tool_call: ToolCall


class DoneEvent(AssistantMessageEvent):
    type: Literal["done"] = "done"
    message: AssistantMessage


class ErrorEvent(AssistantMessageEvent):
    type: Literal["error"] = "error"
    error: str = ""
    message: AssistantMessage | None = None


__all__ = [
    "AssistantMessage",
    "AssistantMessageEvent",
    "Content",
    "Context",
    "DoneEvent",
    "ErrorEvent",
    "ImageContent",
    "Message",
    "Model",
    "StartEvent",
    "StopReason",
    "TextContent",
    "TextDeltaEvent",
    "ThinkingContent",
    "ThinkingDeltaEvent",
    "ToolCall",
    "ToolCallDeltaEvent",
    "ToolCallEndEvent",
    "ToolCallStartEvent",
    "ToolResultMessage",
    "ToolSpec",
    "Usage",
    "UserMessage",
    "now_ms",
]
