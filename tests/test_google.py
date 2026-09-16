from __future__ import annotations

from types import SimpleNamespace

from tm.ai.event_stream import EventStream
from tm.ai.providers.base import StreamOptions
from tm.ai.providers.google import GoogleProvider, messages_to_google
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

MODEL = Model(id="gemini-2.0-flash", provider="google")


def ns(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


class FakeModels:
    def __init__(self, chunks) -> None:
        self._chunks = chunks
        self.last_params: dict = {}

    async def generate_content_stream(self, **params):
        self.last_params = params

        async def _gen():
            for chunk in self._chunks:
                yield chunk

        return _gen()


class FakeAio:
    def __init__(self, chunks) -> None:
        self.models = FakeModels(chunks)


class FakeClient:
    def __init__(self, chunks) -> None:
        self.aio = FakeAio(chunks)


def provider(chunks) -> tuple[GoogleProvider, FakeModels]:
    client = FakeClient(chunks)
    prov = GoogleProvider("google", "Google", [MODEL], client=client)
    return prov, client.aio.models


async def collect(stream: EventStream[AssistantMessageEvent, AssistantMessage]):
    events = [event async for event in stream]
    return events, await stream.result()


def chunk(parts, finish=None, usage=None):
    return ns(
        candidates=[ns(content=ns(parts=parts), finish_reason=finish)],
        usage_metadata=usage,
    )


async def test_streams_text_and_usage() -> None:
    chunks = [
        chunk([ns(text="Hel")]),
        chunk([ns(text="lo")], finish="STOP", usage=ns(
            prompt_token_count=5, candidates_token_count=2, total_token_count=7
        )),
    ]
    prov, models = provider(chunks)
    context = Context(messages=[UserMessage(content="hi")])

    events, final = await collect(prov.stream(MODEL, context))

    assert [e.delta for e in events if e.type == "text_delta"] == ["Hel", "lo"]
    assert final.text() == "Hello"
    assert final.stop_reason == "stop"
    assert final.usage is not None and final.usage.total == 7
    assert models.last_params["contents"][0]["parts"][0]["text"] == "hi"


async def test_streams_thinking() -> None:
    chunks = [
        chunk([ns(text="hmm", thought=True)]),
        chunk([ns(text="answer")], finish="STOP"),
    ]
    prov, _ = provider(chunks)
    context = Context(messages=[UserMessage(content="q")])

    _, final = await collect(prov.stream(MODEL, context))

    assert final.thinking() == "hmm"
    assert final.text() == "answer"


async def test_streams_function_call() -> None:
    chunks = [
        chunk([ns(function_call=ns(name="read", args={"path": "a.txt"}))], finish="STOP"),
    ]
    prov, models = provider(chunks)
    context = Context(
        messages=[UserMessage(content="read")],
        tools=[ToolSpec(name="read", description="read", parameters={"type": "object"})],
    )

    events, final = await collect(prov.stream(MODEL, context))

    assert final.stop_reason == "tool_use"
    assert final.tool_calls[0].name == "read"
    assert final.tool_calls[0].arguments == {"path": "a.txt"}
    assert any(e.type == "toolcall_end" for e in events)
    declarations = models.last_params["config"]["tools"][0]["function_declarations"]
    assert declarations[0]["name"] == "read"


async def test_system_instruction_and_temperature() -> None:
    prov, models = provider([chunk([ns(text="ok")], finish="STOP")])
    context = Context(system_prompt="be nice", messages=[UserMessage(content="hi")])

    await collect(prov.stream(MODEL, context, StreamOptions(temperature=0.1)))

    assert models.last_params["config"]["system_instruction"] == "be nice"
    assert models.last_params["config"]["temperature"] == 0.1


async def test_provider_error_becomes_error_event() -> None:
    class BrokenModels:
        async def generate_content_stream(self, **params):
            raise RuntimeError("boom")

    client = ns(aio=ns(models=BrokenModels()))
    prov = GoogleProvider("google", "Google", [MODEL], client=client)
    context = Context(messages=[UserMessage(content="hi")])

    events, final = await collect(prov.stream(MODEL, context))

    assert any(e.type == "error" for e in events)
    assert final.stop_reason == "error"
    assert final.error_message == "boom"


def test_messages_to_google_groups_tool_results() -> None:
    messages: list[Message] = [
        UserMessage(content="go"),
        AssistantMessage(
            tool_calls=[
                ToolCall(id="1", name="read", arguments={"path": "a"}),
                ToolCall(id="2", name="ls", arguments={}),
            ]
        ),
        ToolResultMessage(tool_call_id="1", tool_name="read", content=[TextContent(text="A")]),
        ToolResultMessage(tool_call_id="2", tool_name="ls", content=[TextContent(text="B")]),
    ]
    converted = messages_to_google(messages)

    assert converted[0] == {"role": "user", "parts": [{"text": "go"}]}
    assert converted[1]["role"] == "model"
    assert converted[1]["parts"][0]["function_call"]["name"] == "read"
    assert converted[2]["role"] == "user"
    assert len(converted[2]["parts"]) == 2
    assert converted[2]["parts"][0]["function_response"]["name"] == "read"
