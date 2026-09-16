"""Tool abstraction shared by the agent loop and built-in tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Generic, Literal, TypeVar

from pydantic import BaseModel

from tm.ai.types import Content, TextContent, ToolSpec
from tm.utils.abort import AbortSignal

P = TypeVar("P", bound=BaseModel)

OnUpdate = Callable[[str], Awaitable[None]]


@dataclass
class ToolContext:
    cwd: Path
    signal: AbortSignal | None = None
    on_update: OnUpdate | None = None


class ToolResult(BaseModel):
    content: list[Content]
    details: dict = {}
    is_error: bool = False
    terminate: bool = False

    def text(self) -> str:
        return "".join(c.text for c in self.content if isinstance(c, TextContent))


def text_result(
    text: str,
    *,
    is_error: bool = False,
    details: dict | None = None,
    terminate: bool = False,
) -> ToolResult:
    return ToolResult(
        content=[TextContent(text=text)],
        details=details or {},
        is_error=is_error,
        terminate=terminate,
    )


class Tool(ABC, Generic[P]):
    """A capability the model can invoke.

    ``parameters_model`` is a pydantic model; its JSON schema is sent to the LLM
    and used to validate arguments before execution.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    parameters_model: ClassVar[type[BaseModel]]
    execution_mode: ClassVar[Literal["parallel", "sequential"]] = "parallel"

    @classmethod
    def spec(cls) -> ToolSpec:
        return ToolSpec(
            name=cls.name,
            description=cls.description,
            parameters=cls.parameters_model.model_json_schema(),
        )

    @abstractmethod
    async def execute(self, call_id: str, args: P, ctx: ToolContext) -> ToolResult:
        raise NotImplementedError


__all__ = ["OnUpdate", "Tool", "ToolContext", "ToolResult", "text_result"]
