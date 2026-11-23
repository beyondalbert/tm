from __future__ import annotations

from tm.ai.estimate import (
    CONTEXT_SAFETY_TOKENS,
    MIN_MAX_TOKENS,
    clamp_max_tokens,
    estimate_context_tokens,
    estimate_tools_tokens,
)
from tm.ai.types import Context, Message, Model, ToolSpec, UserMessage


def test_clamp_fits_output_to_the_window() -> None:
    model = Model(id="m", provider="p", context_window=10_000, max_tokens=8_000)
    context = Context(messages=[UserMessage(content="x" * 400)])

    # 400 chars is about 104 tokens, so 10k - 104 - 4096 leaves 5800.
    assert clamp_max_tokens(model, context, None) == 5_800
    assert clamp_max_tokens(model, context, 2_000) == 2_000


def test_clamp_never_goes_below_the_minimum() -> None:
    model = Model(id="m", provider="p", context_window=1_000, max_tokens=8_000)
    context = Context(messages=[UserMessage(content="x" * 400)])

    assert clamp_max_tokens(model, context, None) == MIN_MAX_TOKENS


def test_clamp_leaves_unknown_windows_alone() -> None:
    model = Model(id="m", provider="p", context_window=0, max_tokens=8_000)
    context = Context(messages=[UserMessage(content="hello")])

    assert clamp_max_tokens(model, context, None) == 8_000
    assert clamp_max_tokens(model, context, 500) == 500


def test_clamp_accounts_for_tool_definitions() -> None:
    model = Model(id="m", provider="p", context_window=13_000, max_tokens=8_000)
    tool = ToolSpec(name="big", description="x" * 4_000)
    assert estimate_tools_tokens([tool]) > 0

    without = Context(messages=[UserMessage(content="hello")])
    with_tool = Context(messages=[UserMessage(content="hello")], tools=[tool])

    assert clamp_max_tokens(model, with_tool, None) < clamp_max_tokens(
        model, without, None
    )


def test_context_estimate_includes_the_safety_margin() -> None:
    model = Model(id="m", provider="p", context_window=10_000, max_tokens=8_000)
    context = Context(messages=[UserMessage(content="x" * 400)])

    expected = 10_000 - estimate_context_tokens(context.messages) - CONTEXT_SAFETY_TOKENS
    assert clamp_max_tokens(model, context, None) == expected


def test_estimate_uses_usage_after_the_last_compaction() -> None:
    from tm.ai.types import AssistantMessage, TextContent, Usage
    from tm.core.compaction import SUMMARY_PREFIX

    messages: list[Message] = [
        UserMessage(content=f"{SUMMARY_PREFIX}summary", timestamp=1000),
        # Retained from before the compaction: its usage describes a longer prefix.
        AssistantMessage(
            content=[TextContent(text="old")],
            stop_reason="stop",
            usage=Usage(input=90000, output=10, total=90010),
            timestamp=900,
        ),
        # Produced after the compaction: a valid anchor for the current prefix.
        AssistantMessage(
            content=[TextContent(text="new")],
            stop_reason="stop",
            usage=Usage(input=2000, output=10, total=2010),
            timestamp=1100,
        ),
    ]

    total = estimate_context_tokens(messages)
    assert 2010 <= total < 5000
