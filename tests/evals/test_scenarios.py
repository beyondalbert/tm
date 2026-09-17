"""Offline eval scenarios run against the faux provider.

These are deterministic and need no API key. They exercise the full agent loop:
scripted turns, tool execution, and termination.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from tests.evals.harness import EvalResult, EvalScenario, format_report, run_scenario
from tm.ai.types import AssistantMessage, TextContent, ToolCall
from tm.tools.base import Tool, ToolContext, ToolResult, text_result

pytestmark = pytest.mark.eval


class AddParams(BaseModel):
    a: int
    b: int


class AddTool(Tool[AddParams]):
    name = "add"
    description = "Add two integers."
    parameters_model = AddParams

    async def execute(self, call_id: str, args: AddParams, ctx: ToolContext) -> ToolResult:
        return text_result(str(args.a + args.b))


def _tool_call_then_stop(text: str) -> list[AssistantMessage]:
    return [
        AssistantMessage(
            tool_calls=[ToolCall(id="t1", name="add", arguments={"a": 2, "b": 3})],
            stop_reason="tool_use",
        ),
        AssistantMessage(content=[TextContent(text=text)], stop_reason="stop"),
    ]


SCENARIOS = [
    EvalScenario(
        name="plain-reply",
        prompt="say hello",
        script=[AssistantMessage(content=[TextContent(text="hello")], stop_reason="stop")],
        expect={"min_messages": 2, "final_text_contains": "hello"},
    ),
    EvalScenario(
        name="single-tool-call",
        prompt="add 2 and 3",
        script=_tool_call_then_stop("the sum is 5"),
        tools=[AddTool()],
        expect={"tool_calls": 1, "min_messages": 4},
    ),
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
async def test_scenario(scenario: EvalScenario) -> None:
    result = await run_scenario(scenario)
    assert result.passed, format_report([result])


async def test_report_formats_failures() -> None:
    failing = EvalResult(name="x", passed=False, notes=["expected 2 tool calls, got 0"])
    report = format_report([failing])
    assert "FAIL" in report
    assert "0/1 scenarios passed" in report
