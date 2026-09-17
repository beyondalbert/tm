"""Live DeepSeek connectivity smoke test.

Requires DEEPSEEK_API_KEY in the environment. Runs:
  1. a raw streaming chat completion (no tools)
  2. a full agent turn that must invoke the shell tool

Usage: python -m uv run python scripts/smoke_deepseek.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from tm.ai.registry import Registry
from tm.ai.types import Context, Model, UserMessage
from tm.core.agent import Agent
from tm.core.events import ToolExecutionEndEvent, ToolExecutionStartEvent
from tm.permissions import (
    AuditLog,
    AutoAllowApprover,
    PermissionChecker,
    Policy,
    build_permission_hook,
)
from tm.tools.registry import build_default_tools


async def test_chat(provider, model: Model) -> None:
    context = Context(
        system_prompt="You are a connectivity probe. Follow instructions exactly.",
        messages=[UserMessage(content="Reply with exactly the token PONG and nothing else.")],
    )
    stream = provider.stream(model, context)
    chunks: list[str] = []
    async for event in stream:
        if event.type == "text_delta":
            chunks.append(event.delta)
    final = await stream.result()
    assert final.stop_reason != "error", f"provider error: {final.error_message}"
    text = "".join(chunks)
    assert "PONG" in text.upper(), f"unexpected reply: {text!r}"
    print(f"[1/2] chat ok: {text.strip()!r} (usage={final.usage})")


async def test_tool_call(provider, model: Model) -> None:
    workdir = Path(tempfile.mkdtemp(prefix="tm-smoke-"))
    policy = Policy.from_dict({"default": "allow"})
    checker = PermissionChecker(
        policy, approver=AutoAllowApprover(), audit=AuditLog(None)
    )
    agent = Agent(
        model,
        provider=provider,
        tools=build_default_tools(),
        system_prompt=(
            "You are a connectivity probe. Use the shell tool when asked to run a command."
        ),
        cwd=workdir,
    )
    agent.before_tool_call = build_permission_hook(checker, workdir)

    started: list[str] = []
    outputs: list[str] = []

    async def listener(event) -> None:
        if isinstance(event, ToolExecutionStartEvent):
            started.append(event.tool_name)
        elif isinstance(event, ToolExecutionEndEvent):
            outputs.append(event.output)

    agent.subscribe(listener)
    await agent.prompt(
        "Run the shell command `echo TM_SMOKE_OK` and then tell me the exact output."
    )

    assert "shell" in started, f"model did not call the shell tool: {started}"
    assert any("TM_SMOKE_OK" in out for out in outputs), f"tool output missing: {outputs}"
    print(f"[2/2] agent tool call ok: tools={started}")


async def main() -> int:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY is not set", file=sys.stderr)
        return 2

    registry = Registry()
    try:
        provider, model = registry.resolve("deepseek-v4-pro", "deepseek")
        print(f"using {model.provider}/{model.id}")
        await test_chat(provider, model)
        await test_tool_call(provider, model)
        print("SMOKE TEST PASSED")
        return 0
    except Exception as exc:  # noqa: BLE001 - report any failure
        print(f"SMOKE TEST FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        await registry.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
