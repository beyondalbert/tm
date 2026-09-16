from types import SimpleNamespace

from tm.ai.event_stream import EventStream
from tm.ai.providers.base import StreamOptions
from tm.ai.providers.openai_compat import OpenAICompatProvider, message_to_openai
from tm.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    Model,
    TextContent,
    ToolCall,
    ToolResultMessage,
    ToolSpec,
    UserMessage,
)


class FakeFunction:
    def __init__(self, name: str | None = None, arguments: str | None = None) -> None:
        self.name = name
        self.arguments = arguments


class FakeToolCallDelta:
    def __init__(self, index: int, id: str | None = None, name=None, arguments=None) -> None:
        self.index = index
        self.id = id
        self.function = FakeFunction(name, arguments)


class FakeDelta:
    def __init__(self, content=None, reasoning_content=None, tool_calls=None) -> None:
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = tool_calls


class FakeChoice:
    def __init__(self, delta=None, finish_reason=None) -> None:
        self.delta = delta
        self.finish_reason = finish_reason


class FakeChunk:
    def __init__(self, choices, usage=None) -> None:
        self.choices = choices
        self.usage = usage


class FakeCompletions:
    def __init__(self, chunks) -> None:
        self._chunks = chunks
        self.last_params: dict = {}

    def create(self, **params):
        self.last_params = params

        async def _gen():
            for chunk in self._chunks:
                yield chunk

        async def _coro():
            return _gen()

        return _coro()


class FakeClient:
    def __init__(self, chunks) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(chunks))

    async def close(self) -> None:
        pass


def _provider(chunks) -> tuple[OpenAICompatProvider, FakeCompletions]:
    client = FakeClient(chunks)
    provider = OpenAICompatProvider(
        "deepseek",
        "DeepSeek",
        [Model(id="deepseek-chat", provider="deepseek")],
        client=client,  # type: ignore[arg-type]
    )
    return provider, client.chat.completions


async def _collect(stream: EventStream[AssistantMessageEvent, AssistantMessage]):
    events = [event async for event in stream]
    return events, await stream.result()


async def test_streams_text_deltas_and_usage() -> None:
    chunks = [
        FakeChunk([FakeChoice(FakeDelta(content="Hel"))]),
        FakeChunk([FakeChoice(FakeDelta(content="lo"))]),
        FakeChunk([FakeChoice(FakeDelta(), finish_reason="stop")], usage=SimpleNamespace(
            prompt_tokens=5, completion_tokens=2, total_tokens=7,
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        )),
    ]
    provider, completions = _provider(chunks)
    context = Context(messages=[UserMessage(content="hi")])

    events, final = await _collect(provider.stream(Model(id="deepseek-chat", provider="deepseek"), context))

    deltas = [e.delta for e in events if e.type == "text_delta"]
    assert deltas == ["Hel", "lo"]
    assert final.text() == "Hello"
    assert final.stop_reason == "stop"
    assert final.usage is not None and final.usage.total == 7
    assert completions.last_params["stream"] is True


async def test_streams_reasoning_content() -> None:
    chunks = [
        FakeChunk([FakeChoice(FakeDelta(reasoning_content="th"))]),
        FakeChunk([FakeChoice(FakeDelta(reasoning_content="ink"))]),
        FakeChunk([FakeChoice(FakeDelta(content="answer"), finish_reason="stop")]),
    ]
    provider, _ = _provider(chunks)
    context = Context(messages=[UserMessage(content="q")])

    events, final = await _collect(provider.stream(Model(id="deepseek-reasoner", provider="deepseek"), context))

    reasoning = "".join(e.delta for e in events if e.type == "thinking_delta")
    assert reasoning == "think"
    assert final.thinking() == "think"
    assert final.text() == "answer"


async def test_accumulates_streamed_tool_call_arguments() -> None:
    chunks = [
        FakeChunk([FakeChoice(FakeDelta(tool_calls=[FakeToolCallDelta(0, id="call_1", name="read", arguments='{"pa')]))]),
        FakeChunk([FakeChoice(FakeDelta(tool_calls=[FakeToolCallDelta(0, arguments='th":"a.txt"}')]))]),
        FakeChunk([FakeChoice(FakeDelta(), finish_reason="tool_calls")]),
    ]
    provider, completions = _provider(chunks)
    context = Context(
        messages=[UserMessage(content="read a.txt")],
        tools=[ToolSpec(name="read", description="read", parameters={"type": "object"})],
    )

    events, final = await _collect(provider.stream(Model(id="deepseek-chat", provider="deepseek"), context))

    assert [e.type for e in events if e.type.startswith("toolcall")] == [
        "toolcall_start",
        "toolcall_delta",
        "toolcall_delta",
        "toolcall_end",
    ]
    assert final.stop_reason == "tool_use"
    assert len(final.tool_calls) == 1
    assert final.tool_calls[0].name == "read"
    assert final.tool_calls[0].arguments == {"path": "a.txt"}
    assert completions.last_params["tool_choice"] == "auto"
    assert completions.last_params["tools"][0]["function"]["name"] == "read"


async def test_provider_error_becomes_error_event() -> None:
    class BrokenCompletions:
        def create(self, **params):
            async def _coro():
                raise RuntimeError("no network")

            return _coro()

    client = SimpleNamespace(chat=SimpleNamespace(completions=BrokenCompletions()))
    provider = OpenAICompatProvider(
        "deepseek",
        "DeepSeek",
        [Model(id="deepseek-chat", provider="deepseek")],
        client=client,  # type: ignore[arg-type]
    )
    context = Context(messages=[UserMessage(content="hi")])

    events, final = await _collect(provider.stream(Model(id="deepseek-chat", provider="deepseek"), context))

    assert any(e.type == "error" for e in events)
    assert final.stop_reason == "error"
    assert final.error_message == "no network"


def test_message_to_openai_conversions() -> None:
    user = message_to_openai(UserMessage(content="hello"))
    assert user == {"role": "user", "content": "hello"}

    assistant = AssistantMessage(
        content=[TextContent(text="calling")],
        tool_calls=[ToolCall(id="t1", name="ls", arguments={"path": "."})],
    )
    converted = message_to_openai(assistant)
    assert converted["tool_calls"][0]["function"]["arguments"] == '{"path": "."}'

    result = message_to_openai(
        ToolResultMessage(
            tool_call_id="t1",
            tool_name="ls",
            content=[TextContent(text="a.txt")],
        )
    )
    assert result == {"role": "tool", "tool_call_id": "t1", "content": "a.txt"}


async def test_aborted_signal_stops_stream() -> None:
    from tm.utils.abort import AbortSignal

    signal = AbortSignal()
    signal.abort()
    chunks = [
        FakeChunk([FakeChoice(FakeDelta(content="x"))]),
        FakeChunk([FakeChoice(FakeDelta(), finish_reason="stop")]),
    ]
    provider, _ = _provider(chunks)
    context = Context(messages=[UserMessage(content="hi")])

    _, final = await _collect(
        provider.stream(
            Model(id="deepseek-chat", provider="deepseek"),
            context,
            StreamOptions(signal=signal),
        )
    )
    assert final.stop_reason == "aborted"
