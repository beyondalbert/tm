"""Live DeepSeek smoke test, skipped unless DEEPSEEK_API_KEY is set."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DEEPSEEK_API_KEY"),
    reason="DEEPSEEK_API_KEY not set",
)


async def test_deepseek_chat() -> None:
    from tm.ai.registry import Registry
    from tm.ai.types import Context, TextDeltaEvent, UserMessage

    registry = Registry()
    try:
        provider, model = registry.resolve("deepseek-chat", "deepseek")
        context = Context(messages=[UserMessage(content="Reply with exactly: PONG")])
        stream = provider.stream(model, context)
        text = "".join(
            [event.delta async for event in stream if isinstance(event, TextDeltaEvent)]
        )
        final = await stream.result()
    finally:
        await registry.aclose()

    assert final.stop_reason != "error", final.error_message
    assert "PONG" in text.upper()
