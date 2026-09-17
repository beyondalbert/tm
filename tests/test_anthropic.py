from __future__ import annotations

from types import SimpleNamespace

from tm.ai.event_stream import EventStream
from tm.ai.providers.anthropic import AnthropicProvider, messages_to_anthropic
from tm.ai.providers.base import StreamOptions
from tm.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    Message,
    Model,
    TextContent,
    ToolCall,
    ToolResultMessage,
    ToolSpec,
    UserMessage,
)

MODEL = Model(id="claude-3-5-sonnet-latest", provider="anthropic")


def ns(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


class FakeMessages:
    def __init__(self, events) -> None:
        self._events = events
        self.last_params: dict = {}

    def create(self, **params):
        self.last_params = params

        async def _gen():
            for event in self._events:
                yield event

        async def _coro():
            return _gen()

        return _coro()


class FakeClient:
    def __init__(self, events) -> None:
        self.messages = FakeMessages(events)

    async def close(self) -> None:
        pass


def provider(events) -> tuple[AnthropicProvider, FakeMessages]:
    client = FakeClient(events)
    return (
        AnthropicProvider(
            "anthropic",
            "Anthropic",
            [MODEL],
            client=client,
        ),
        client.messages,
    )


async def collect(stream: EventStream[AssistantMessageEvent, AssistantMessage]):
    events = [event async for event in stream]
    return events, await stream.result()


def text_events() -> list:
    return [
        ns(type="message_start", message=ns(usage=ns(input_tokens=10))),
        ns(type="content_block_start", index=0, content_block=ns(type="text", text="")),
        ns(type="content_block_delta", index=0, delta=ns(type="text_delta", text="Hel")),
        ns(type="content_block_delta", index=0, delta=ns(type="text_delta", text="lo")),
        ns(type="content_block_stop", index=0),
        ns(type="message_delta", delta=ns(stop_reason="end_turn"), usage=ns(output_tokens=2)),
        ns(type="message_stop"),
    ]


async def test_streams_text_and_usage() -> None:
    prov, messages = provider(text_events())
    context = Context(messages=[UserMessage(content="hi")])

    events, final = await collect(prov.stream(MODEL, context))

    assert [e.delta for e in events if e.type == "text_delta"] == ["Hel", "lo"]
    assert final.text() == "Hello"
    assert final.stop_reason == "stop"
    assert final.usage is not None and final.usage.total == 12
    assert messages.last_params["stream"] is True


async def test_streams_tool_use() -> None:
    events_source = [
        ns(type="message_start", message=ns(usage=ns(input_tokens=5))),
        ns(
            type="content_block_start",
            index=0,
            content_block=ns(type="tool_use", id="toolu_1", name="read"),
        ),
        ns(
            type="content_block_delta",
            index=0,
            delta=ns(type="input_json_delta", partial_json='{"path"'),
        ),
        ns(
            type="content_block_delta",
            index=0,
            delta=ns(type="input_json_delta", partial_json=':"a.txt"}'),
        ),
        ns(type="content_block_stop", index=0),
        ns(type="message_delta", delta=ns(stop_reason="tool_use"), usage=ns(output_tokens=7)),
        ns(type="message_stop"),
    ]
    prov, messages = provider(events_source)
    context = Context(
        messages=[UserMessage(content="read a.txt")],
        tools=[ToolSpec(name="read", description="read", parameters={"type": "object"})],
    )

    _, final = await collect(prov.stream(MODEL, context))

    assert final.stop_reason == "tool_use"
    assert final.tool_calls[0].name == "read"
    assert final.tool_calls[0].arguments == {"path": "a.txt"}
    assert messages.last_params["tools"][0]["name"] == "read"


async def test_streams_thinking() -> None:
    events_source = [
        ns(type="message_start", message=ns(usage=ns(input_tokens=1))),
        ns(type="content_block_start", index=0, content_block=ns(type="thinking", thinking="")),
        ns(type="content_block_delta", index=0, delta=ns(type="thinking_delta", thinking="hmm")),
        ns(type="content_block_stop", index=0),
        ns(type="content_block_start", index=1, content_block=ns(type="text", text="")),
        ns(type="content_block_delta", index=1, delta=ns(type="text_delta", text="answer")),
        ns(type="content_block_stop", index=1),
        ns(type="message_delta", delta=ns(stop_reason="end_turn"), usage=ns(output_tokens=3)),
        ns(type="message_stop"),
    ]
    prov, _ = provider(events_source)
    context = Context(messages=[UserMessage(content="q")])

    _, final = await collect(prov.stream(MODEL, context))

    assert final.thinking() == "hmm"
    assert final.text() == "answer"


async def test_provider_error_becomes_error_event() -> None:
    class BrokenMessages:
        def create(self, **params):
            async def _coro():
                raise RuntimeError("boom")

            return _coro()

    client = SimpleNamespace(messages=BrokenMessages())
    prov = AnthropicProvider("anthropic", "Anthropic", [MODEL], client=client)
    context = Context(messages=[UserMessage(content="hi")])

    events, final = await collect(prov.stream(MODEL, context))

    assert any(e.type == "error" for e in events)
    assert final.stop_reason == "error"


async def test_cache_control_when_long() -> None:
    prov, messages = provider(text_events())
    context = Context(
        system_prompt="be nice",
        messages=[UserMessage(content="hi")],
        tools=[ToolSpec(name="read", description="read", parameters={"type": "object"})],
    )

    await collect(prov.stream(MODEL, context, StreamOptions(cache_retention="long")))

    params = messages.last_params
    assert params["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert params["tools"][-1]["cache_control"]["ttl"] == "1h"


def test_messages_to_anthropic_groups_tool_results() -> None:
    messages: list[Message] = [
        UserMessage(content="go"),
        AssistantMessage(
            content=[TextContent(text="calling")],
            tool_calls=[
                ToolCall(id="t1", name="read", arguments={"path": "a"}),
                ToolCall(id="t2", name="ls", arguments={}),
            ],
        ),
        ToolResultMessage(tool_call_id="t1", tool_name="read", content=[TextContent(text="A")]),
        ToolResultMessage(tool_call_id="t2", tool_name="ls", content=[TextContent(text="B")]),
    ]
    converted = messages_to_anthropic(messages)

    assert converted[0] == {"role": "user", "content": [{"type": "text", "text": "go"}]}
    assert converted[1]["role"] == "assistant"
    assert converted[1]["content"][1]["type"] == "tool_use"
    assert converted[2]["role"] == "user"
    assert len(converted[2]["content"]) == 2
    assert converted[2]["content"][0]["tool_use_id"] == "t1"
