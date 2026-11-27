from __future__ import annotations

import asyncio
from pathlib import Path

from tests.rpc_fixtures import finished, hanging, make_host, scripted
from tests.test_rpc_server import RpcHarness, StubHandler
from tm.permissions.actions import Action, ActionKind
from tm.rpc.acp import AcpApprover, AcpServer
from tm.rpc.host import ServerHost, SessionOptions, Wire


async def _updates(harness: RpcHarness, timeout: float = 2.0) -> list[dict]:
    message = await harness.wait_notification("session/update", timeout)
    return [message]


async def test_initialize_advertises_the_subset(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    async with RpcHarness(AcpServer(host)) as harness:
        info = await harness.result("initialize", {"protocolVersion": 1})
        assert info["protocolVersion"] == 1
        assert info["agentCapabilities"]["loadSession"] is True
        assert await harness.result("authenticate", {"methodId": "x"}) == {}


async def test_session_new_and_prompt_stream_text(tmp_path: Path) -> None:
    sub = tmp_path / "pkg"
    sub.mkdir()
    host = make_host(tmp_path, [[finished("default")], [finished("hello there")]])
    async with RpcHarness(AcpServer(host)) as harness:
        created = await harness.result("session/new", {"cwd": str(sub), "mcpServers": []})
        session_id = created["sessionId"]

        result = await harness.result(
            "session/prompt",
            {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": "say hi"}],
            },
        )
        assert result == {"stopReason": "end_turn"}

        updates = [
            message["params"]["update"]
            for message in harness.messages
            if message.get("method") == "session/update"
        ]
        chunks = [
            update["content"]["text"]
            for update in updates
            if update.get("sessionUpdate") == "agent_message_chunk"
        ]
        assert "".join(chunks) == "hello there"


async def test_session_load_reports_unknown_sessions(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    async with RpcHarness(AcpServer(host)) as harness:
        info = await harness.result("initialize", {})
        assert info["agentCapabilities"]["loadSession"] is True
        error = await harness.error(
            "session/load", {"sessionId": "missing", "cwd": str(tmp_path)}
        )
        assert error["code"] == -32007


async def test_session_cancel_stops_the_turn(tmp_path: Path) -> None:
    host = make_host(
        tmp_path, streams=[scripted([finished("init")]), hanging]
    )
    async with RpcHarness(AcpServer(host)) as harness:
        info = await harness.result("session/new", {"cwd": str(tmp_path)})
        session_id = info["sessionId"]
        pending = asyncio.ensure_future(
            harness.result(
                "session/prompt",
                {"sessionId": session_id, "prompt": [{"type": "text", "text": "work"}]},
            )
        )
        runtime = host._sessions[session_id]
        while runtime.turn is None:
            await asyncio.sleep(0.005)

        await harness.feed(
            '{"jsonrpc":"2.0","method":"session/cancel",'
            f'"params":{{"sessionId":"{session_id}"}}}}'
        )
        assert await pending == {"stopReason": "cancelled"}


async def test_prompt_requires_a_text_block(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    async with RpcHarness(AcpServer(host)) as harness:
        info = await harness.result("session/new", {"cwd": str(tmp_path)})
        error = await harness.error(
            "session/prompt", {"sessionId": info["sessionId"], "prompt": []}
        )
        assert "text block" in error["message"]


async def test_acp_approver_maps_the_selected_option(tmp_path: Path) -> None:
    wire = Wire()
    approver = AcpApprover(wire, session_id="s1", timeout_ms=500)
    action = Action(kind=ActionKind.FILE_WRITE, target="src/a.py", tool_name="edit")
    async with RpcHarness(StubHandler()) as harness:
        wire.bind(harness.server.send, harness.server.request)
        pending = asyncio.ensure_future(approver.request(action, "policy says ask"))
        request = await harness.wait_notification("session/request_permission")
        params = request["params"]
        assert params["sessionId"] == "s1"
        assert params["toolCall"]["kind"] == "edit"
        assert [option["optionId"] for option in params["options"]] == [
            "allow",
            "allow_scope",
            "deny",
        ]

        await harness.send_client_result(
            request["id"], {"outcome": {"outcome": "selected", "optionId": "allow_scope"}}
        )
        outcome = await pending
        assert outcome.allowed is True
        assert outcome.remember is True
        assert outcome.source == "host"


async def test_acp_approver_denies_when_the_client_disconnects(tmp_path: Path) -> None:
    wire = Wire()
    approver = AcpApprover(wire, session_id=None, timeout_ms=50)
    action = Action(kind=ActionKind.SHELL, target="ls", tool_name="shell")
    async with RpcHarness(StubHandler()) as harness:
        wire.bind(harness.server.send, harness.server.request)
        outcome = await approver.request(action, "policy says ask")
        assert outcome.allowed is False
        assert outcome.source == "host-timeout"


def _host_with_policy_ask(tmp_path: Path) -> ServerHost:
    return make_host(tmp_path, [[finished("x")]])


async def test_session_new_rejects_a_missing_cwd(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    async with RpcHarness(AcpServer(host)) as harness:
        await harness.result("initialize", {})
        error = await harness.error(
            "session/new", {"cwd": str(tmp_path / "nope"), "mcpServers": []}
        )
        assert "cwd is not a directory" in error["message"]


def test_default_access_is_normal(tmp_path: Path) -> None:
    host = _host_with_policy_ask(tmp_path)
    runtime = host._sessions[host._default_id]  # type: ignore[index]
    assert runtime.options.access == "normal"
    assert isinstance(runtime.options, SessionOptions)


async def test_tool_calls_become_acp_updates(tmp_path: Path) -> None:
    from tests.rpc_fixtures import MODEL
    from tests.test_agent import EchoTool
    from tm.ai.types import AssistantMessage, ToolCall
    from tm.core.agent import Agent
    from tm.rpc.host import BuiltSession

    tool_call = AssistantMessage(
        tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "hi"})],
        stop_reason="tool_use",
    )

    def builder(options, session, session_id, wire):
        agent = Agent(
            MODEL,
            stream_fn=scripted([tool_call, finished("done")]),
            tools=[EchoTool()],
            cwd=options.cwd,
        )
        return BuiltSession(agent=agent)

    host = ServerHost(builder, manager=None, initial=SessionOptions(cwd=tmp_path))
    async with RpcHarness(AcpServer(host)) as harness:
        created = await harness.result("session/new", {"cwd": str(tmp_path)})
        result = await harness.result(
            "session/prompt",
            {
                "sessionId": created["sessionId"],
                "prompt": [{"type": "text", "text": "echo hi"}],
            },
        )
        assert result == {"stopReason": "end_turn"}

        updates = [
            message["params"]["update"]
            for message in harness.messages
            if message.get("method") == "session/update"
        ]
        started = [u for u in updates if u.get("sessionUpdate") == "tool_call"]
        ended = [u for u in updates if u.get("sessionUpdate") == "tool_call_update"]
        assert started and started[0]["toolCallId"] == "t1"
        assert started[0]["title"] == "echo"
        assert started[0]["status"] == "in_progress"
        assert ended and ended[0]["status"] == "completed"
        assert ended[0]["content"][0]["content"]["text"] == "hi"


async def test_session_load_accepts_an_open_session(tmp_path: Path) -> None:
    host = make_host(tmp_path, [[finished("x")], [finished("y")]])
    async with RpcHarness(AcpServer(host)) as harness:
        await harness.result("initialize", {})
        created = await harness.result("session/new", {"cwd": str(tmp_path)})
        assert await harness.result(
            "session/load", {"sessionId": created["sessionId"], "cwd": str(tmp_path)}
        ) == {}
