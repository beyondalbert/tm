from __future__ import annotations

from pathlib import Path

from tm.ai.types import AssistantMessage, TextContent, ToolResultMessage, UserMessage
from tm.core.session import SessionManager


def test_session_roundtrip(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path, name="test")
    session.append(UserMessage(content="hello"))
    session.append(AssistantMessage(content=[TextContent(text="hi")], stop_reason="stop"))

    reloaded = manager.open(session.path)
    messages = reloaded.messages()
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[1].role == "assistant"
    assert reloaded.name == "test"


def test_session_preserves_tool_messages(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path)
    session.append(
        ToolResultMessage(
            tool_call_id="t1", tool_name="ls", content=[TextContent(text="a.txt")]
        )
    )
    reloaded = manager.open(session.path)
    message = reloaded.messages()[0]
    assert message.role == "tool"
    assert message.content[0].text == "a.txt"  # type: ignore[union-attr]


def test_continue_recent_filters_by_cwd(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    other = tmp_path / "other"
    other.mkdir()
    old = manager.create(cwd=tmp_path)
    old.append(UserMessage(content="first"))
    manager.create(cwd=other)

    recent = manager.continue_recent(tmp_path)
    assert recent is not None
    assert recent.id == old.id
    assert manager.continue_recent(tmp_path / "missing") is None


def test_list_sessions(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    manager.create(cwd=tmp_path, name="a")
    manager.create(cwd=tmp_path, name="b")
    infos = manager.list()
    assert len(infos) == 2
    assert {info.name for info in infos} == {"a", "b"}


def test_list_includes_stats(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path, name="stats")
    session.append(UserMessage(content="hello world"))
    session.append(AssistantMessage(content=[TextContent(text="hi")], stop_reason="stop"))

    infos = manager.list()
    assert len(infos) == 1
    info = infos[0]
    assert info.message_count == 2
    assert info.preview == "hello world"
    assert info.updated >= info.created


def test_agent_persists_messages_to_session(tmp_path: Path) -> None:
    import asyncio

    from tm.ai.event_stream import EventStream
    from tm.ai.types import DoneEvent, Model, StartEvent
    from tm.core.agent import Agent

    def stream_fn(model, context, options) -> EventStream:
        message = AssistantMessage(content=[TextContent(text="done")], stop_reason="stop")
        stream: EventStream = EventStream()
        stream.push(StartEvent(partial=message))
        stream.push(DoneEvent(partial=message, message=message))
        stream.end(message)
        return stream

    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path)
    agent = Agent(Model(id="fake", provider="fake"), stream_fn=stream_fn, session=session)

    asyncio.run(agent.prompt("hi"))

    reloaded = manager.open(session.path)
    roles = [message.role for message in reloaded.messages()]
    assert roles == ["user", "assistant"]


def test_agent_resume_loads_history(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path)
    session.append(UserMessage(content="earlier"))
    session.append(AssistantMessage(content=[TextContent(text="reply")], stop_reason="stop"))

    from tm.ai.types import Model
    from tm.core.agent import Agent

    agent = Agent(Model(id="fake", provider="fake"), stream_fn=lambda *a: None)  # type: ignore[arg-type]
    agent.resume(session)
    assert len(agent.messages) == 2
    assert agent.session is session


def test_agent_constructor_loads_session_history(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path)
    session.append(UserMessage(content="loaded"))

    from tm.ai.types import Model
    from tm.core.agent import Agent

    agent = Agent(
        Model(id="fake", provider="fake"),
        stream_fn=lambda *a: None,  # type: ignore[arg-type]
        session=session,
    )
    assert [m.content for m in agent.messages] == ["loaded"]


def test_branch_from_changes_active_path(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path)
    first = session.append(UserMessage(content="m1"))
    session.append(UserMessage(content="m2"))
    session.append(UserMessage(content="m3"))
    assert [m.content for m in session.messages()] == ["m1", "m2", "m3"]

    session.branch_from(first.id)
    session.append(UserMessage(content="m2b"))
    assert [m.content for m in session.messages()] == ["m1", "m2b"]


def test_branch_survives_reopen(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path)
    first = session.append(UserMessage(content="a"))
    session.append(UserMessage(content="b"))
    session.branch_from(first.id)
    session.append(UserMessage(content="c"))

    reopened = manager.open(session.path)
    assert [m.content for m in reopened.messages()] == ["a", "c"]


def test_fork_copies_branch_to_new_file(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path, name="orig")
    session.append(UserMessage(content="a"))
    second = session.append(UserMessage(content="b"))
    session.append(UserMessage(content="c"))

    forked = manager.fork(session, second.id, cwd=tmp_path)

    assert forked.id != session.id
    assert forked.path != session.path
    assert forked.name == "orig"
    assert [m.content for m in forked.messages()] == ["a", "b"]

    reopened = manager.open(forked.path)
    assert [m.content for m in reopened.messages()] == ["a", "b"]


def test_points_and_path_to(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path)
    first = session.append(UserMessage(content="one"))
    session.append(UserMessage(content="two"))

    points = session.points()
    assert len(points) == 2
    assert [entry.id for entry in session.path_to(points[-1].id)] == [
        session._entries[0].id,  # meta
        first.id,
        points[-1].id,
    ]
