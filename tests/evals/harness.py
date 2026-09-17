"""Eval harness: scenario runner with a report.

Adapted from Pi's ``evals`` package. An eval is a named scenario that runs the
agent against the faux provider (offline, deterministic) and asserts on the
outcome. The harness records a small report so failures are legible.

Run the offline eval suite::

    uv run pytest tests/evals -q

Real-provider evals are marked ``live`` and skip without credentials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.evals.faux_provider import FAUX_MODEL, FauxProvider
from tm.ai.types import AssistantMessage, Message
from tm.core.agent import Agent
from tm.tools.base import Tool


@dataclass
class EvalResult:
    name: str
    passed: bool
    messages: list[Message] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class EvalScenario:
    """One named scenario: a user prompt, a script, and expected checks."""

    name: str
    prompt: str
    script: list[AssistantMessage]
    tools: list[Tool] = field(default_factory=list)
    expect: dict[str, Any] = field(default_factory=dict)


async def run_scenario(scenario: EvalScenario, *, cwd: Path | None = None) -> EvalResult:
    provider = FauxProvider(script=scenario.script)
    agent = Agent(FAUX_MODEL, provider=provider, tools=scenario.tools, cwd=cwd)
    try:
        await agent.prompt(scenario.prompt)
    except Exception as exc:  # noqa: BLE001 - an eval records the failure
        return EvalResult(name=scenario.name, passed=False, error=f"{type(exc).__name__}: {exc}")

    messages = agent.messages
    notes: list[str] = []
    passed = True

    expect = scenario.expect
    if "min_messages" in expect and len(messages) < expect["min_messages"]:
        passed = False
        notes.append(f"expected >= {expect['min_messages']} messages, got {len(messages)}")
    if "tool_calls" in expect:
        actual = sum(len(m.tool_calls) for m in messages if isinstance(m, AssistantMessage))
        if actual != expect["tool_calls"]:
            passed = False
            notes.append(f"expected {expect['tool_calls']} tool calls, got {actual}")
    if "final_text_contains" in expect:
        last_assistant = next(
            (m for m in reversed(messages) if isinstance(m, AssistantMessage)), None
        )
        text = last_assistant.text() if last_assistant else ""
        if expect["final_text_contains"] not in text:
            passed = False
            notes.append(f"final text missing {expect['final_text_contains']!r}")

    return EvalResult(name=scenario.name, passed=passed, messages=messages, notes=notes)


def format_report(results: list[EvalResult]) -> str:
    lines: list[str] = []
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        lines.append(f"[{status}] {result.name}")
        for note in result.notes:
            lines.append(f"       - {note}")
        if result.error:
            lines.append(f"       ! {result.error}")
    passed = sum(1 for r in results if r.passed)
    lines.append(f"\n{passed}/{len(results)} scenarios passed")
    return "\n".join(lines)


__all__ = ["EvalResult", "EvalScenario", "format_report", "run_scenario"]
