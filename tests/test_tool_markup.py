from __future__ import annotations

from tm.ai.types import AssistantMessage, TextContent
from tm.core.messages import is_looping_text, repair_tool_messages
from tm.core.tool_markup import (
    has_raw_tool_markup,
    parse_raw_tool_calls,
    strip_tool_markup,
)

FULLWIDTH = "\uff5c"


def dsml(tag: str) -> str:
    return f"<{FULLWIDTH}DSML{FULLWIDTH}{tag}>"


def close(tag: str) -> str:
    return f"</{FULLWIDTH}DSML{FULLWIDTH}{tag}>"


def test_has_raw_tool_markup_detects_ascii_variants() -> None:
    assert has_raw_tool_markup("done\n</invoke>") is True
    assert has_raw_tool_markup("x\n</parameter>") is True
    assert has_raw_tool_markup("<tool_call>") is True
    assert has_raw_tool_markup("<|tool_call>") is True


def test_has_raw_tool_markup_detects_fullwidth_dsml() -> None:
    assert has_raw_tool_markup("Let me read it.\n\n" + dsml("invoke")) is True
    assert has_raw_tool_markup(close("parameter")) is True
    assert has_raw_tool_markup(close("invoke")) is True
    assert has_raw_tool_markup(close("tool_calls")) is True
    assert has_raw_tool_markup(dsml("/invoke")) is True


def test_has_raw_tool_markup_ignores_plain_prose() -> None:
    assert has_raw_tool_markup("") is False
    assert has_raw_tool_markup("the DSML format is discussed here") is False
    assert has_raw_tool_markup("use invoke tool calls") is False


def test_parse_single_fullwidth_call() -> None:
    text = (
        dsml("tool_calls")
        + "\n"
        + dsml('invoke name="read"')
        + "\n"
        + dsml('parameter name="path" string="true"')
        + "D:\\proj\\a.py"
        + close("parameter")
        + "\n"
        + close("invoke")
        + "\n"
        + close("tool_calls")
    )
    calls = parse_raw_tool_calls(text)

    assert len(calls) == 1
    assert calls[0].name == "read"
    assert calls[0].arguments == {"path": "D:\\proj\\a.py"}
    assert calls[0].id


def test_parse_multiple_calls_and_json_arguments() -> None:
    text = (
        dsml('invoke name="find"')
        + dsml('parameter name="pattern" string="true"')
        + "*.kt"
        + close("parameter")
        + dsml('parameter name="max_results" string="false"')
        + "40"
        + close("parameter")
        + close("invoke")
        + dsml('invoke name="read"')
        + dsml('parameter name="path" string="true"')
        + "a.kt"
        + close("parameter")
        + close("invoke")
    )
    calls = parse_raw_tool_calls(text)

    assert [call.name for call in calls] == ["find", "read"]
    assert calls[0].arguments == {"pattern": "*.kt", "max_results": 40}
    assert calls[1].arguments == {"path": "a.kt"}
    assert calls[0].id != calls[1].id


def test_parse_ascii_and_nested_json_body() -> None:
    text = (
        '<invoke name="edit"><parameter name="edits" string="false">'
        '[{"a": 1}]</parameter></invoke>'
    )
    calls = parse_raw_tool_calls(text)
    assert calls[0].arguments == {"edits": [{"a": 1}]}

    bare = '<invoke name="edit">{"path": "b.py"}</invoke>'
    assert parse_raw_tool_calls(bare)[0].arguments == {"path": "b.py"}


def test_parse_slash_after_namespace_variant() -> None:
    text = dsml('invoke name="read"') + "x" + dsml("/invoke")
    calls = parse_raw_tool_calls(text)
    assert [call.name for call in calls] == ["read"]


def test_parse_skips_unknown_tools() -> None:
    text = (
        dsml('invoke name="nope"')
        + close("invoke")
        + dsml('invoke name="read"')
        + close("invoke")
    )
    calls = parse_raw_tool_calls(text, {"read"})
    assert [call.name for call in calls] == ["read"]


def test_parse_ignores_truncated_call() -> None:
    text = dsml('invoke name="read"') + dsml('parameter name="path" string="true"') + "a"
    assert parse_raw_tool_calls(text) == []


def test_strip_tool_markup_keeps_prose() -> None:
    text = (
        "Now I have the contract. Let me read the types.\n\n"
        + close("parameter")
        + "\n"
        + close("invoke")
        + "\n"
        + close("tool_calls")
    )
    assert strip_tool_markup(text) == "Now I have the contract. Let me read the types."


def test_strip_tool_markup_removes_invocations_only() -> None:
    text = (
        "before\n"
        + dsml('invoke name="read"')
        + dsml('parameter name="path" string="true"')
        + "a.py"
        + close("parameter")
        + close("invoke")
        + "\nafter"
    )
    assert strip_tool_markup(text) == "before\n\nafter"


def test_leaked_dsml_markup_is_looping_text() -> None:
    text = "Let me read the file.\n\n" + close("invoke")
    assert is_looping_text(text) is True


def test_repair_drops_dsml_markup_from_kept_tool_calls() -> None:
    message = AssistantMessage(
        content=[TextContent(text="reading\n" + close("invoke"))],
        stop_reason="tool_use",
    )
    assert repair_tool_messages([message]) == []
