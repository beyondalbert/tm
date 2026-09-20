from __future__ import annotations

from tm.ai.types import (
    AssistantMessage,
    Message,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from tm.core.messages import repair_tool_messages


def test_orphan_tool_result_is_dropped() -> None:
    messages: list[Message] = [
        ToolResultMessage(tool_call_id="ghost", tool_name="read", content=[TextContent(text="x")]),
        UserMessage(content="hi"),
    ]
    repaired = repair_tool_messages(messages)
    assert repaired == [messages[1]]


def test_tool_call_without_result_is_trimmed() -> None:
    assistant = AssistantMessage(
        content=[TextContent(text="calling")],
        tool_calls=[ToolCall(id="t1", name="read", arguments={})],
    )
    repaired = repair_tool_messages([assistant, UserMessage(content="next")])
    kept = repaired[0]
    assert isinstance(kept, AssistantMessage)
    assert kept.tool_calls == []
    assert kept.text() == "calling"


def test_empty_assistant_is_dropped() -> None:
    empty = AssistantMessage(content=[], stop_reason="error")
    repaired = repair_tool_messages([UserMessage(content="hi"), empty])
    assert repaired == [UserMessage(content="hi")]


def test_assistant_trimmed_to_empty_is_dropped() -> None:
    # a tool-only assistant whose result was compacted away must not become an
    # assistant with neither content nor tool_calls
    assistant = AssistantMessage(
        content=[],
        tool_calls=[ToolCall(id="t1", name="read", arguments={})],
    )
    repaired = repair_tool_messages([UserMessage(content="q"), assistant])
    assert repaired == [UserMessage(content="q")]


def test_valid_sequence_is_unchanged() -> None:
    messages: list[Message] = [
        UserMessage(content="q"),
        AssistantMessage(
            content=[TextContent(text="call")],
            tool_calls=[ToolCall(id="t1", name="read", arguments={})],
        ),
        ToolResultMessage(tool_call_id="t1", tool_name="read", content=[TextContent(text="data")]),
    ]
    assert repair_tool_messages(messages) == messages


def test_partial_results_keep_only_answered_calls() -> None:
    assistant = AssistantMessage(
        content=[TextContent(text="calling")],
        tool_calls=[
            ToolCall(id="t1", name="read", arguments={}),
            ToolCall(id="t2", name="read", arguments={}),
        ],
    )
    repaired = repair_tool_messages(
        [
            assistant,
            ToolResultMessage(tool_call_id="t1", tool_name="read", content=[TextContent(text="a")]),
        ]
    )
    kept = repaired[0]
    assert isinstance(kept, AssistantMessage)
    assert [call.id for call in kept.tool_calls] == ["t1"]
