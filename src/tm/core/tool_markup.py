"""Detect and recover raw tool-call markup that leaked into assistant text.

Some OpenAI-compatible servers do not recognize every tool-call format a model
can emit, so the raw markup lands in the reply text instead of in structured
``tool_calls``. DeepSeek, for example, wraps calls as
``<｜DSML｜invoke name="read">`` using U+FF5C FULLWIDTH VERTICAL LINE instead of
``|``; matching only the ASCII spelling misses that variant entirely.

The agent loop treats leaked markup as a parser failure. When the leaked markup
is complete, the calls are reconstructed and executed instead of ending the
run.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Collection

from tm.ai.types import ToolCall

#: Vertical bars models use in namespaced tags, including the fullwidth variant.
_PIPE = "[|\uff5c\u2223\u2502\u2758\u01c0]"
#: Optional namespace prefix such as ``|DSML|`` between ``<`` and the tag name.
_NS = rf"(?:{_PIPE}\s*dsml\s*{_PIPE})?"


def _close(name: str) -> str:
    # Both ``</|DSML|invoke>`` and ``<|DSML|/invoke>`` appear in the wild.
    return rf"(?:<\s*{_NS}\s*/\s*{name}\s*>|</\s*{_NS}\s*{name}\s*>)"


_INVOKE = re.compile(
    rf"<\s*{_NS}\s*invoke\s+name\s*=\s*[\"'](?P<name>[^\"']+)[\"'][^>]*>"
    rf"(?P<body>.*?)"
    rf"{_close('invoke')}",
    re.S | re.I,
)
_PARAMETER = re.compile(
    rf"<\s*{_NS}\s*parameter\s+name\s*=\s*[\"'](?P<name>[^\"']+)[\"']"
    rf"(?P<attrs>[^>]*)>(?P<value>.*?)"
    rf"{_close('parameter')}",
    re.S | re.I,
)
_NON_STRING = re.compile(r"string\s*=\s*[\"']false[\"']", re.I)
_DSML_TAG = re.compile(rf"</?\s*{_PIPE}\s*dsml\s*{_PIPE}", re.I)
_DSML_CALL_TAG = re.compile(rf"</?\s*{_PIPE}\s*dsml\s*{_PIPE}\s*\w*\s*>", re.I)
_STRAY_TAG = re.compile(
    rf"(?:<\s*{_NS}\s*/\s*|</\s*{_NS}\s*)"
    r"(?:tool_calls|invoke|parameter|function_call|tool_call)\b[^>]*>",
    re.I,
)
_STRAY_PIPE_TAG = re.compile(r"</?\s*\|?\s*tool_calls?_begin\s*\|?\s*>", re.I)

#: Substrings that only appear when a tool call leaked into the text.
_RAW_MARKERS = (
    "</parameter>",
    "<tool_call>",
    "</tool_call>",
    "<|tool_call",
    "<tool_calls_begin|>",
    "<|tool_calls_begin|>",
    "<function_call>",
    "</invoke>",
)


def _normalize_pipes(text: str) -> str:
    return re.sub(_PIPE, "|", text)


def has_raw_tool_markup(text: str) -> bool:
    """Whether assistant text contains an unparsed tool call."""
    if not text:
        return False
    normalized = _normalize_pipes(text).lower()
    if _DSML_TAG.search(normalized):
        return True
    return any(marker in normalized for marker in _RAW_MARKERS)


def _argument(value: str, attrs: str) -> object:
    if _NON_STRING.search(attrs):
        try:
            return json.loads(value.strip())
        except json.JSONDecodeError:
            return value
    return value


def _parse_arguments(body: str) -> dict:
    params = list(_PARAMETER.finditer(body))
    if params:
        return {
            match.group("name").strip(): _argument(
                match.group("value"), match.group("attrs")
            )
            for match in params
        }
    stripped = body.strip()
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def parse_raw_tool_calls(
    text: str, tool_names: Collection[str] | None = None
) -> list[ToolCall]:
    """Rebuild tool calls from leaked markup.

    Only invocations with a complete opening and closing tag are returned, so a
    reply cut off mid-call yields nothing rather than half-filled arguments.
    Calls naming tools the agent does not have are skipped when ``tool_names``
    is given.
    """
    calls: list[ToolCall] = []
    for match in _INVOKE.finditer(text):
        name = match.group("name").strip()
        if not name:
            continue
        if tool_names is not None and name not in tool_names:
            continue
        calls.append(
            ToolCall(
                id=f"raw-{uuid.uuid4().hex[:12]}",
                name=name,
                arguments=_parse_arguments(match.group("body")),
            )
        )
    return calls


def strip_tool_markup(text: str) -> str:
    """Remove leaked tool-call markup, keeping the surrounding prose."""
    cleaned = _INVOKE.sub("", text)
    cleaned = _DSML_CALL_TAG.sub("", cleaned)
    cleaned = _STRAY_TAG.sub("", cleaned)
    cleaned = _STRAY_PIPE_TAG.sub("", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


__all__ = [
    "has_raw_tool_markup",
    "parse_raw_tool_calls",
    "strip_tool_markup",
]
