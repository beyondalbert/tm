from __future__ import annotations

from pathlib import Path

import pytest

from tm.ai.event_stream import EventStream
from tm.ai.registry import Registry
from tm.ai.types import AssistantMessage, DoneEvent, Model, StartEvent, TextContent, UserMessage
from tm.cli.commands import CommandContext, ExitSignal, SlashCommands
from tm.core.agent import Agent
from tm.extensions import ExtensionAPI
from tm.prompts import PromptTemplate
from tm.skills import Skill

MODEL = Model(id="fake", provider="fake")


def make_agent(messages=None, summary: str = "summary") -> Agent:
    def stream_fn(model, context, options) -> EventStream:
        message = AssistantMessage(content=[TextContent(text=summary)], stop_reason="stop")
        stream: EventStream = EventStream()
        stream.push(StartEvent(partial=message))
        stream.push(DoneEvent(partial=message, message=message))
        stream.end(message)
        return stream

    agent = Agent(MODEL, stream_fn=stream_fn)
    if messages:
        agent.set_messages(messages)
    return agent


def make_commands(
    tmp_path: Path,
    agent: Agent | None = None,
    session=None,
    session_manager=None,
    picker=None,
):
    emitted: list[str] = []
    extensions = ExtensionAPI()
    extensions.register_command("hello", lambda arg: f"hi {arg}")
    skills = [Skill(name="review", description="review code", path=tmp_path, body="Review steps")]
    templates = {
        "summarize": PromptTemplate(
            name="summarize", path=tmp_path, text="Summarize this: {{input}}"
        )
    }
    ctx = CommandContext(
        agent=agent or make_agent(),
        registry=Registry(),
        cwd=tmp_path,
        emit=emitted.append,
        session=session,
        session_manager=session_manager,
        skills=skills,
        templates=templates,
        extensions=extensions,
        picker=picker,
    )
    return SlashCommands(ctx), emitted, ctx


async def test_help_and_unknown(tmp_path: Path) -> None:
    commands, emitted, _ = make_commands(tmp_path)
    assert await commands.handle("help") is None
    assert "commands:" in emitted[-1]

    await commands.handle("nope")
    assert "unknown command" in emitted[-1]


async def test_exit_raises(tmp_path: Path) -> None:
    commands, _, _ = make_commands(tmp_path)
    with pytest.raises(ExitSignal):
        await commands.handle("exit")


async def test_model_switch(tmp_path: Path) -> None:
    agent = make_agent()
    commands, emitted, _ = make_commands(tmp_path, agent)

    await commands.handle("model")
    assert "deepseek: deepseek-v4-pro, deepseek-flash" in emitted[-1]

    await commands.handle("model deepseek-v4-pro")
    assert agent.model.id == "deepseek-v4-pro"
    assert agent.provider is not None and agent.provider.id == "deepseek"

    await commands.handle("model deepseek-flash")
    assert agent.model.id == "deepseek-flash"


async def test_skill_and_skills(tmp_path: Path) -> None:
    commands, emitted, _ = make_commands(tmp_path)
    await commands.handle("skills")
    assert "review" in emitted[-1]

    forwarded = await commands.handle("skill:review")
    assert forwarded is not None and "Review steps" in forwarded


async def test_prompt_template_expansion(tmp_path: Path) -> None:
    commands, _, _ = make_commands(tmp_path)
    forwarded = await commands.handle("summarize src/app.py")
    assert forwarded == "Summarize this: src/app.py"


async def test_extension_command(tmp_path: Path) -> None:
    commands, emitted, _ = make_commands(tmp_path)
    await commands.handle("hello world")
    assert emitted[-1] == "hi world"


async def test_new_resets_conversation(tmp_path: Path) -> None:
    agent = make_agent(messages=[UserMessage(content="old")])
    commands, emitted, _ = make_commands(tmp_path, agent)
    await commands.handle("new")
    assert agent.messages == []
    assert "cleared" in emitted[-1]


async def test_compact_command(tmp_path: Path) -> None:
    agent = make_agent(messages=[UserMessage(content=f"m{i}") for i in range(10)])
    commands, emitted, _ = make_commands(tmp_path, agent)
    await commands.handle("compact")
    assert "compacted" in emitted[-1]
    assert len(agent.messages) == 7


async def test_session_info_without_session(tmp_path: Path) -> None:
    commands, emitted, _ = make_commands(tmp_path)
    await commands.handle("session")
    assert "no session" in emitted[-1]


async def test_tree_lists_and_branches(tmp_path: Path) -> None:
    from tm.core.session import SessionManager

    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path)
    session.append(UserMessage(content="one"))
    session.append(UserMessage(content="two"))
    agent = make_agent()
    agent.resume(session)
    commands, emitted, ctx = make_commands(
        tmp_path, agent, session=session, session_manager=manager
    )

    await commands.handle("tree")
    assert "1. one" in emitted[-1]
    assert "2. two" in emitted[-1]

    await commands.handle("tree 1")
    assert "branched" in emitted[-1]
    assert [m.content for m in agent.messages] == ["one"]


async def test_fork_creates_new_session(tmp_path: Path) -> None:
    from tm.core.session import SessionManager

    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path)
    session.append(UserMessage(content="a"))
    session.append(UserMessage(content="b"))
    agent = make_agent()
    agent.resume(session)
    commands, emitted, ctx = make_commands(
        tmp_path, agent, session=session, session_manager=manager
    )

    await commands.handle("fork 1")
    assert "forked to session" in emitted[-1]
    assert ctx.session is not None and ctx.session.path != session.path
    assert [m.content for m in agent.messages] == ["a"]


async def test_tree_requires_session(tmp_path: Path) -> None:
    commands, emitted, _ = make_commands(tmp_path)
    await commands.handle("tree")
    assert "no session" in emitted[-1]
    await commands.handle("fork")
    assert "no session" in emitted[-1]


async def test_resume_by_id(tmp_path: Path) -> None:
    from tm.core.session import SessionManager

    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path, name="target")
    session.append(UserMessage(content="hello"))
    session.append(UserMessage(content="world"))

    agent = make_agent()
    commands, emitted, ctx = make_commands(
        tmp_path, agent, session=None, session_manager=manager
    )
    await commands.handle(f"resume {session.id[:6]}")

    assert "resumed session" in emitted[-1]
    assert ctx.session is not None
    assert [m.content for m in agent.messages] == ["hello", "world"]


async def test_resume_with_picker(tmp_path: Path) -> None:
    from tm.core.session import SessionManager

    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path, name="pick me")
    session.append(UserMessage(content="picked"))

    async def picker(infos):
        return infos[0]

    agent = make_agent()
    commands, emitted, ctx = make_commands(
        tmp_path, agent, session=None, session_manager=manager, picker=picker
    )
    await commands.handle("resume")

    assert "resumed session" in emitted[-1]
    assert [m.content for m in agent.messages] == ["picked"]


async def test_resume_no_sessions(tmp_path: Path) -> None:
    from tm.core.session import SessionManager

    manager = SessionManager(tmp_path / "empty")
    commands, emitted, _ = make_commands(
        tmp_path, make_agent(), session=None, session_manager=manager
    )
    await commands.handle("resume")
    assert "no saved sessions" in emitted[-1]


def test_match_session(tmp_path: Path) -> None:
    from tm.core.session import SessionInfo

    infos = [
        SessionInfo(id="aaaa1111", path=tmp_path / "a.jsonl", name="one", cwd=str(tmp_path), created=1),
        SessionInfo(id="bbbb2222", path=tmp_path / "b.jsonl", name="two", cwd=str(tmp_path), created=2),
    ]
    assert SlashCommands._match_session(infos, "1") is infos[0]
    assert SlashCommands._match_session(infos, "bbbb") is infos[1]
    assert SlashCommands._match_session(infos, "two") is infos[1]
    assert SlashCommands._match_session(infos, "9") is None


def test_match_session_all_digit_id(tmp_path: Path) -> None:
    from tm.core.session import SessionInfo

    numeric = SessionInfo(
        id="893491abcdef",
        path=tmp_path / "n.jsonl",
        name=None,
        cwd=str(tmp_path),
        created=1,
    )
    other = SessionInfo(
        id="aaaa0000", path=tmp_path / "o.jsonl", name="x", cwd=str(tmp_path), created=2
    )
    infos = [numeric, other]
    assert SlashCommands._match_session(infos, "893491") is numeric
    assert SlashCommands._match_session(infos, "2") is other


async def test_recover_command_reports_nothing(tmp_path: Path) -> None:
    commands, emitted, _ = make_commands(tmp_path)
    await commands.handle("recover")
    assert "nothing to recover" in emitted[-1]


async def test_recover_command_reruns_pending(tmp_path: Path) -> None:
    from pydantic import BaseModel

    from tm.ai.types import ToolCall
    from tm.core.operation import Operation, PendingEffect
    from tm.core.session import SessionManager
    from tm.core.store import Store
    from tm.tools.base import Tool, ToolContext, ToolResult, text_result

    class NoParams(BaseModel):
        pass

    class ReplayTool(Tool[NoParams]):
        name = "rt"
        description = "Replay-safe."
        parameters_model = NoParams
        replay_safe = True

        def __init__(self) -> None:
            self.calls = 0

        async def execute(
            self, call_id: str, args: NoParams, ctx: ToolContext
        ) -> ToolResult:
            self.calls += 1
            return text_result("replayed")

    manager = SessionManager(tmp_path / "sessions")
    session = manager.create(cwd=tmp_path)
    session.append(UserMessage(content="go"))
    session.append(
        AssistantMessage(
            tool_calls=[ToolCall(id="c1", name="rt", arguments={})],
            stop_reason="tool_use",
        )
    )
    store = Store()
    operation = Operation.accept("op1", store, session_id=session.id)
    operation.begin_effect(
        PendingEffect(tool_name="rt", call_id="c1", arguments={}, replay_safe=True), turn=1
    )

    tool = ReplayTool()
    agent = make_agent()
    agent.store = store
    agent.tools = [tool]
    agent.resume(session)
    commands, emitted, _ = make_commands(
        tmp_path, agent, session=session, session_manager=manager
    )

    await commands.handle("recover")

    assert "recovered 1 interrupted operation(s)" in emitted[-1]
    assert tool.calls == 1
