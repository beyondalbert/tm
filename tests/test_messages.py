from __future__ import annotations

from tm.ai.types import (
    AssistantMessage,
    Message,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from tm.core.messages import is_looping_text, repair_tool_messages, strip_loop_output


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
    user = UserMessage(content="hi")
    empty = AssistantMessage(content=[], stop_reason="error")
    repaired = repair_tool_messages([user, empty])
    assert repaired == [user]


def test_assistant_trimmed_to_empty_is_dropped() -> None:
    # a tool-only assistant whose result was compacted away must not become an
    # assistant with neither content nor tool_calls
    user = UserMessage(content="q")
    assistant = AssistantMessage(
        content=[],
        tool_calls=[ToolCall(id="t1", name="read", arguments={})],
    )
    repaired = repair_tool_messages([user, assistant])
    assert repaired == [user]


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


def test_looping_assistant_text_is_stripped_but_tool_calls_kept() -> None:
    assistant = AssistantMessage(
        content=[TextContent(text="Let me call it.\n\n<to\n</parameter>\nread.")],
        tool_calls=[ToolCall(id="t1", name="read", arguments={})],
    )
    result = ToolResultMessage(
        tool_call_id="t1", tool_name="read", content=[TextContent(text="data")]
    )
    repaired = repair_tool_messages([assistant, result])
    kept = repaired[0]
    assert isinstance(kept, AssistantMessage)
    assert kept.text() == ""
    assert [call.id for call in kept.tool_calls] == ["t1"]
    assert repaired[1] == result


def test_looping_assistant_without_tool_calls_is_dropped() -> None:
    user = UserMessage(content="q")
    looping = AssistantMessage(
        content=[TextContent(text="Running.\n" * 20)], stop_reason="stop"
    )
    assert is_looping_text(looping.text()) is True
    assert repair_tool_messages([user, looping]) == [user]


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


def test_strip_loop_output_drops_looping_assistant_text() -> None:
    user = UserMessage(content="q")
    looping = AssistantMessage(
        content=[TextContent(text="Running.\n" * 20)], stop_reason="stop"
    )
    kept, dropped = strip_loop_output([user, looping])
    assert kept == [user]
    assert dropped == [looping]


def test_strip_loop_output_keeps_tool_calls_and_results() -> None:
    assistant = AssistantMessage(
        content=[TextContent(text="Let me call it.\n\n<to\n</parameter>\nread.")],
        tool_calls=[ToolCall(id="t1", name="read", arguments={})],
    )
    result = ToolResultMessage(
        tool_call_id="t1", tool_name="read", content=[TextContent(text="data")]
    )
    kept, dropped = strip_loop_output([assistant, result])
    assert dropped == []
    assert kept == [assistant, result]
    assert assistant.text() == ""
    assert [call.id for call in assistant.tool_calls] == ["t1"]


def test_strip_loop_output_collapses_identical_tool_groups() -> None:
    def group(index: int) -> list[Message]:
        return [
            AssistantMessage(
                tool_calls=[
                    ToolCall(id=f"t{index}", name="echo", arguments={"text": "same"})
                ],
            ),
            ToolResultMessage(
                tool_call_id=f"t{index}",
                tool_name="echo",
                content=[TextContent(text="same")],
            ),
        ]

    messages: list[Message] = [UserMessage(content="go"), *group(1), *group(2), *group(3)]
    kept, dropped = strip_loop_output(messages)
    kept_calls = [
        call.id
        for message in kept
        if isinstance(message, AssistantMessage)
        for call in message.tool_calls
    ]
    assert kept_calls == ["t1"]
    assert len(dropped) == 4
