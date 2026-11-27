from __future__ import annotations

from pathlib import Path
from typing import Any

from tm.ai.event_stream import EventStream
from tm.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
)
from tm.core.agent import Agent
from tm.core.session import Session, SessionManager
from tm.permissions import AutoDenyApprover
from tm.rpc.host import BuiltSession, ServerHost, SessionOptions, Wire
from tm.safety import Journal

MODEL = Model(id="fake-model", provider="fake")


def finished(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text=text)], stop_reason="stop")


def scripted(messages: list[AssistantMessage]):
    iterator = iter(messages)

    def stream_fn(model: Model, context: Context, options: object) -> EventStream:
        message = next(iterator)
        stream: EventStream = EventStream()
        stream.push(StartEvent(partial=message))
        if message.text():
            stream.push(TextDeltaEvent(partial=message, delta=message.text()))
        stream.push(DoneEvent(partial=message, message=message))
        stream.end(message)
        return stream

    return stream_fn


def hanging(*_args: object) -> EventStream:
    """A stream that never finishes on its own; only abort ends it."""
    return EventStream()


class Scripted:
    """Session builder that plays a script per session and records the options."""

    def __init__(
        self,
        root: Path,
        scripts: list[list[AssistantMessage]] | None = None,
        *,
        journal: Journal | None = None,
        streams: list[Any] | None = None,
    ) -> None:
        self.root = root
        self.scripts = list(scripts or [])
        self.streams = list(streams or [])
        self.journal = journal or Journal(root / "changes" / "changes.jsonl")
        self.options: list[SessionOptions] = []
        self.approvers: list[Any] = []

    def __call__(
        self,
        options: SessionOptions,
        session: Session | None,
        session_id: str,
        wire: Wire,
    ) -> BuiltSession:
        self.options.append(options)
        if self.streams:
            stream_fn = self.streams.pop(0)
        else:
            messages = self.scripts.pop(0) if self.scripts else [finished("ok")]
            stream_fn = scripted(messages)
        agent = Agent(
            MODEL,
            stream_fn=stream_fn,
            tools=[],
            cwd=options.cwd,
            session=session,
        )
        approver = AutoDenyApprover()
        self.approvers.append(approver)
        return BuiltSession(agent=agent, approver=approver, journal=self.journal)


def make_host(
    root: Path,
    scripts: list[list[AssistantMessage]] | None = None,
    *,
    manager: bool = True,
    mode: str = "policy",
    max_sessions: int = 4,
    streams: list[Any] | None = None,
) -> ServerHost:
    builder = Scripted(root, scripts, streams=streams)
    session_manager = SessionManager(root / "sessions") if manager else None
    return ServerHost(
        builder,
        manager=session_manager,
        mode=mode,
        max_sessions=max_sessions,
        initial=SessionOptions(cwd=root),
    )
