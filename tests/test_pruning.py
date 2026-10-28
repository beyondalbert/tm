from __future__ import annotations

from pathlib import Path

from tm.ai.event_stream import EventStream
from tm.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    Message,
    Model,
    StartEvent,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from tm.config import Settings
from tm.core.agent import Agent
from tm.core.pruning import (
    PRUNE_PREFIX,
    PrunePolicy,
    policy_from_settings,
    prune_tool_results,
)

MODEL = Model(id="fake", provider="fake")


def _turn(index: int, *, text: str = "x" * 200) -> list[Message]:
    return [
        AssistantMessage(tool_calls=[ToolCall(id=f"t{index}", name="read", arguments={})]),
        ToolResultMessage(
            tool_call_id=f"t{index}", tool_name="read", content=[TextContent(text=text)]
        ),
    ]


def _history(turns: int) -> list[Message]:
    messages: list[Message] = []
    for index in range(turns):
        messages.append(UserMessage(content=f"u{index}"))
        messages.extend(_turn(index))
    return messages


def test_prunes_old_large_results_and_keeps_recent(tmp_path: Path) -> None:
    messages = _history(5)
    policy = PrunePolicy(
        enabled=True, keep_turns=2, min_chars=100, keep_chars=20, spill_dir=tmp_path
    )

    view = prune_tool_results(messages, policy)

    results = [m for m in view if isinstance(m, ToolResultMessage)]
    assert len(results) == 5
    # Turns 0-2 are older than the last two assistant turns.
    assert [m.text().startswith(PRUNE_PREFIX) for m in results] == [True, True, True, False, False]
    # The session keeps the full text.
    original = [m for m in messages if isinstance(m, ToolResultMessage)]
    assert all(m.text() == "x" * 200 for m in original)

    stub = results[0].text()
    assert "200 chars" in stub
    assert stub.rstrip().endswith("x" * 20)
    spill = next(tmp_path.glob("prune-*.log"))
    assert spill.read_text(encoding="utf-8") == "x" * 200


def test_pruning_is_idempotent(tmp_path: Path) -> None:
    policy = PrunePolicy(
        enabled=True, keep_turns=0, min_chars=10, keep_chars=0, spill_dir=tmp_path
    )
    view = prune_tool_results(_history(2), policy)
    again = prune_tool_results(view, policy)
    assert [m.text() for m in again if isinstance(m, ToolResultMessage)] == [
        m.text() for m in view if isinstance(m, ToolResultMessage)
    ]


def test_pruning_skips_small_and_disabled(tmp_path: Path) -> None:
    messages = _history(3)
    small = PrunePolicy(enabled=True, keep_turns=0, min_chars=10_000, spill_dir=tmp_path)
    assert prune_tool_results(messages, small) is messages
    disabled = PrunePolicy(enabled=False)
    assert prune_tool_results(messages, disabled) is messages


def test_policy_from_settings_resolves_spill_dir(tmp_path: Path) -> None:
    policy = policy_from_settings(
        Settings(prune_tool_output=True, spill_dir=str(tmp_path), read_dedupe=False)
    )
    assert policy.enabled is True
    assert policy.spill_dir == tmp_path
    assert policy.keep_turns == 8


async def test_request_view_is_pruned_but_history_is_not(tmp_path: Path) -> None:
    seen: list[list[Message]] = []

    def stream_fn(model: Model, context: Context, options) -> EventStream:
        seen.append(context.messages)
        message = AssistantMessage(content=[TextContent(text="done")], stop_reason="stop")
        stream: EventStream = EventStream()
        stream.push(StartEvent(partial=message))
        stream.push(DoneEvent(partial=message, message=message))
        stream.end(message)
        return stream

    agent = Agent(
        MODEL,
        stream_fn=stream_fn,
        prune=PrunePolicy(
            enabled=True, keep_turns=1, min_chars=100, keep_chars=10, spill_dir=tmp_path
        ),
    )
    agent.set_messages(_history(2))

    await agent.prompt("go")

    request = seen[0]
    sent = [m for m in request if isinstance(m, ToolResultMessage)]
    assert sent[0].text().startswith(PRUNE_PREFIX)
    assert sent[1].text() == "x" * 200
    kept = [m for m in agent.messages if isinstance(m, ToolResultMessage)]
    assert [m.text() for m in kept] == ["x" * 200, "x" * 200]


async def test_compaction_runs_mid_turn_with_token_budget(tmp_path: Path) -> None:
    calls = {"n": 0}

    def stream_fn(model: Model, context: Context, options) -> EventStream:
        calls["n"] += 1
        if calls["n"] == 1:
            message = AssistantMessage(
                content=[TextContent(text="x" * 4000)],
                tool_calls=[ToolCall(id="t1", name="missing", arguments={})],
                stop_reason="tool_use",
            )
        elif calls["n"] == 2:
            message = AssistantMessage(
                content=[TextContent(text="earlier summary")], stop_reason="stop"
            )
        else:
            message = AssistantMessage(content=[TextContent(text="done")], stop_reason="stop")
        stream: EventStream = EventStream()
        stream.push(StartEvent(partial=message))
        stream.push(DoneEvent(partial=message, message=message))
        stream.end(message)
        return stream

    model = Model(id="fake", provider="fake", context_window=1_000_000)
    agent = Agent(
        model,
        stream_fn=stream_fn,
        auto_compact=True,
        compact_threshold=0.8,
        compact_max_tokens=200,
        compact_keep_recent=2,
    )
    agent.set_messages([UserMessage(content="start")])

    await agent.prompt("go")

    assert calls["n"] == 3  # request, mid-turn summary, retried request
    assert isinstance(agent.messages[0], UserMessage)
    assert "earlier summary" in agent.messages[0].content
