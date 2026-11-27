from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from tests.rpc_fixtures import Scripted, finished, hanging, make_host, scripted
from tests.test_rpc_server import RpcHarness
from tm.ai.event_stream import EventStream
from tm.ai.types import Context, Model
from tm.core.agent import Agent
from tm.permissions import AutoDenyApprover
from tm.permissions.actions import Action, ActionKind
from tm.rpc.approver import RpcApprover
from tm.rpc.host import BuiltSession, ServerHost, SessionOptions, SessionRuntime, Wire
from tm.safety import Journal, record_file_change


async def test_initialize_reports_capabilities(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    async with RpcHarness(host) as harness:
        result = await harness.result("initialize", {"clientInfo": {"name": "test"}})
        assert result["protocolVersion"] == "1.0"
        assert result["mode"] == "policy"
        assert result["capabilities"] == {
            "streaming": True,
            "approval": False,
            "sessions": True,
            "hostTools": False,
            "structuredChanges": True,
            "modelSelection": False,
            "hostCredentials": False,
        }
        assert result["accessModes"] == ["normal", "read-only", "dry-run"]
        assert result["maxSessions"] == 4
        assert result["session"]["sessionId"]
        assert "session/create" in result["methods"]


async def test_prompt_streams_events_and_ends_the_turn(tmp_path: Path) -> None:
    host = make_host(tmp_path, [[finished("all done")]])
    async with RpcHarness(host) as harness:
        await harness.result("initialize")
        started = await harness.result("prompt", {"content": "do it"})
        assert started["status"] == "running"
        turn_id = started["turnId"]

        end = await harness.wait_notification("turn/end")
        payload = end["params"]
        assert payload["turnId"] == turn_id
        assert payload["status"] == "done"
        assert payload["text"] == "all done"
        assert payload["changedFiles"] == []

        events = [
            message["params"]
            for message in harness.messages
            if message.get("method") == "event"
        ]
        kinds = [event["event"]["type"] for event in events]
        assert "agent_start" in kinds
        assert "agent_end" in kinds
        assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
        assert all(event["turnId"] == turn_id for event in events)


async def test_busy_turn_rejects_a_second_prompt_and_cancel_aborts(
    tmp_path: Path,
) -> None:
    host = make_host(tmp_path, streams=[hanging])
    async with RpcHarness(host) as harness:
        first = await harness.result("prompt", {"content": "hang"})
        error = await harness.error("prompt", {"content": "again"})
        assert error["code"] == -32001

        assert await harness.result("steer", {"content": "hurry"}) == {}
        assert await harness.result("cancel", {"turnId": first["turnId"]}) == {}
        end = await harness.wait_notification("turn/end")
        assert end["params"]["status"] == "aborted"


async def test_idempotency_key_replays_the_turn(tmp_path: Path) -> None:
    host = make_host(tmp_path, streams=[hanging])
    async with RpcHarness(host) as harness:
        first = await harness.result(
            "prompt", {"content": "work", "idempotencyKey": "k1"}
        )
        replay = await harness.result(
            "prompt", {"content": "work", "idempotencyKey": "k1"}
        )
        assert replay["turnId"] == first["turnId"]
        assert replay["replayed"] is True
        await harness.result("cancel", {})
        end = await harness.wait_notification("turn/end")
        assert end["params"]["status"] == "aborted"

        # Replaying a finished turn re-announces its outcome, so a client that
        # was not connected for the original notification can still finish.
        again = await harness.result(
            "prompt", {"content": "work", "idempotencyKey": "k1"}
        )
        assert again["replayed"] is True
        ends = [
            message["params"]
            for message in harness.messages
            if message.get("method") == "turn/end"
            and message["params"]["turnId"] == first["turnId"]
        ]
        while len(ends) < 2:
            await asyncio.sleep(0.005)
            ends = [
                message["params"]
                for message in harness.messages
                if message.get("method") == "turn/end"
                and message["params"]["turnId"] == first["turnId"]
            ]
        assert ends[-1]["status"] == "aborted"


async def test_control_methods_need_a_running_turn(tmp_path: Path) -> None:
    host = make_host(tmp_path, [[finished("x")]])
    async with RpcHarness(host) as harness:
        assert (await harness.error("cancel"))["code"] == -32002
        assert (await harness.error("steer", {"content": "x"}))["code"] == -32002
        assert (await harness.error("followUp", {"content": "x"}))["code"] == -32002
        unknown = await harness.error("cancel", {"turnId": "other"})
        assert unknown["code"] == -32002


async def test_unknown_session_is_reported(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    async with RpcHarness(host) as harness:
        error = await harness.error("session/info", {"sessionId": "nope"})
        assert error["code"] == -32007
        assert error["data"] == {"sessionId": "nope"}


async def test_approve_without_bridge_is_unsupported(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    async with RpcHarness(host) as harness:
        error = await harness.error(
            "approve", {"requestId": "p-1", "decision": "allow"}
        )
        assert error["code"] == -32004


async def test_approve_routes_to_the_bridge(tmp_path: Path) -> None:
    requests: list[dict] = []

    def builder(
        options: SessionOptions,
        session: object,
        session_id: str,
        wire: Wire,
    ) -> BuiltSession:
        async def record(payload: dict) -> None:
            requests.append(payload)

        approver = RpcApprover(record, timeout_ms=500, session_id=session_id)
        agent = Agent(
            Model(id="fake-model", provider="fake"),
            stream_fn=scripted([finished("ok")]),
            tools=[],
        )
        return BuiltSession(agent=agent, approver=approver)

    host = ServerHost(builder, mode="host", initial=SessionOptions(cwd=tmp_path))
    async with RpcHarness(host) as harness:
        info = await harness.result("initialize", {})
        assert info["capabilities"]["approval"] is True

        pending = asyncio.ensure_future(
            approver_request(host, Action(kind=ActionKind.SHELL, target="pytest -q"))
        )
        while not requests:
            await asyncio.sleep(0.005)
        request_id = requests[0]["params"]["requestId"]

        assert await harness.result(
            "approve", {"requestId": request_id, "decision": "allow_scope"}
        ) == {"resolved": True}
        outcome = await pending
        assert outcome.allowed is True
        assert outcome.remember is True

        duplicate = await harness.error(
            "approve", {"requestId": request_id, "decision": "deny"}
        )
        assert "unknown or already answered" in duplicate["message"]

        bad = await harness.error("approve", {"requestId": "p-404", "decision": "maybe"})
        assert "decision must be one of" in bad["message"]


async def approver_request(host: ServerHost, action: Action):
    runtime = host._sessions[host._default_id]  # type: ignore[index]
    return await runtime.approver.request(action, "policy says ask")  # type: ignore[union-attr]


async def test_turn_reports_changed_files(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("print('hi')\n", encoding="utf-8")
    journal = Journal(tmp_path / "changes" / "changes.jsonl")
    sessions: list[str] = []

    def stream_fn(model: Model, context: Context, options: object) -> EventStream:
        # Record a change at the moment the model answers, i.e. inside the turn.
        record_file_change(
            journal,
            session=sessions[0],
            tool="edit",
            path=target,
            existed=False,
            content=None,
            summary="created app.py",
        )
        return scripted([finished("wrote it")])(model, context, options)

    def builder(options, session, session_id, wire):
        sessions.append(session_id)
        agent = Agent(
            Model(id="fake-model", provider="fake"),
            stream_fn=stream_fn,
            tools=[],
            session=session,
            cwd=options.cwd,
        )
        return BuiltSession(agent=agent, approver=AutoDenyApprover(), journal=journal)

    host = ServerHost(builder, manager=None, initial=SessionOptions(cwd=tmp_path))
    async with RpcHarness(host) as harness:
        await harness.result("prompt", {"content": "write it"})
        end = await harness.wait_notification("turn/end")
        changed = end["params"]["changedFiles"]
        assert [item["path"] for item in changed] == [str(target)]
        assert changed[0]["added"] == 1
        assert changed[0]["removed"] == 0
        assert "print('hi')" in changed[0]["diff"]


async def test_prompt_requires_content(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    async with RpcHarness(host) as harness:
        error = await harness.error("prompt", {})
        assert "content must be a non-empty string" in error["message"]


async def test_policy_ask_is_bridged_and_audited(tmp_path: Path) -> None:
    from tm.ai.types import AssistantMessage, ToolCall
    from tm.permissions import AuditLog, PermissionChecker, Policy, build_permission_hook
    from tm.tools.write import WriteTool

    audit_path = tmp_path / "audit.jsonl"
    requests: list[dict] = []
    target = tmp_path / "out.txt"
    tool_call = AssistantMessage(
        tool_calls=[
            ToolCall(
                id="t1",
                name="write",
                arguments={"path": str(target), "content": "hello\\n"},
            )
        ],
        stop_reason="tool_use",
    )

    def builder(options, session, session_id, wire):
        async def record(payload: dict) -> None:
            requests.append(payload)
            await wire.send(payload)

        approver = RpcApprover(record, timeout_ms=2000, session_id=session_id)
        checker = PermissionChecker(
            Policy(default="ask", cwd=tmp_path),
            approver=approver,
            audit=AuditLog(audit_path),
        )
        agent = Agent(
            Model(id="fake-model", provider="fake"),
            stream_fn=scripted([tool_call, finished("done")]),
            tools=[WriteTool()],
            cwd=tmp_path,
        )
        agent.before_tool_call = build_permission_hook(checker, tmp_path)
        return BuiltSession(agent=agent, approver=approver)

    host = ServerHost(builder, mode="host", initial=SessionOptions(cwd=tmp_path))
    async with RpcHarness(host) as harness:
        await harness.result("prompt", {"content": "echo hi"})
        request = await harness.wait_notification("permission/request")
        params = request["params"]
        assert params["action"]["tool"] == "write"
        assert params["action"]["kind"] == "file_write"

        assert await harness.result(
            "approve", {"requestId": params["requestId"], "decision": "allow"}
        ) == {"resolved": True}
        end = await harness.wait_notification("turn/end")
        assert end["params"]["status"] == "done"
        assert end["params"]["text"] == "done"
        assert target.read_text(encoding="utf-8") == "hello\\n"

    audit = audit_path.read_text(encoding="utf-8")
    assert "host decision" in audit
    assert "user decision" not in audit


async def test_session_compact_without_history_reports_false(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    async with RpcHarness(host) as harness:
        result = await harness.result("session/compact", {})
        assert result == {"compacted": False, "messageCount": 0}


async def test_yolo_approval_is_audited_as_a_host_decision(tmp_path: Path) -> None:
    from tm.permissions import (
        ApprovalMemory,
        AuditLog,
        AutoAllowApprover,
        PermissionChecker,
        Policy,
    )
    from tm.permissions.actions import Action, ActionKind, Decision

    audit_path = tmp_path / "audit.jsonl"
    checker = PermissionChecker(
        Policy(default=Decision.ASK, cwd=tmp_path),
        approver=AutoAllowApprover(source="host"),
        audit=AuditLog(audit_path),
        memory=ApprovalMemory(),
    )
    action = Action(
        kind=ActionKind.FILE_WRITE, target=str(tmp_path / "a.txt"), tool_name="write"
    )
    assert await checker.authorize(action) is True
    assert "host decision" in audit_path.read_text(encoding="utf-8")


async def test_session_create_passes_the_model_choice(tmp_path: Path) -> None:
    host = make_host(tmp_path, [[finished("ok")]])
    builder = host._builder
    assert isinstance(builder, Scripted)
    async with RpcHarness(host) as harness:
        info = await harness.result("session/create", {"model": "fake/fake-model"})
        assert info["sessionId"]
    assert builder.options[-1].model == "fake/fake-model"


async def test_session_set_model_uses_the_injected_switcher(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def switcher(runtime: SessionRuntime, reference: str) -> dict[str, Any]:
        calls.append((runtime.id, reference))
        return {
            "model": {"provider": "other", "id": "big-model"},
            "warnings": ["provider 'other' has no API key"],
        }

    builder = Scripted(tmp_path)
    host = ServerHost(
        builder,
        manager=None,
        mode="policy",
        initial=SessionOptions(cwd=tmp_path),
        model_switcher=switcher,
    )
    async with RpcHarness(host) as harness:
        info = await harness.result("initialize", {})
        assert info["capabilities"]["modelSelection"] is True
        session_id = info["session"]["sessionId"]

        switched = await harness.result(
            "session/set-model", {"sessionId": session_id, "model": "other/big-model"}
        )
        assert switched["model"] == {"provider": "other", "id": "big-model"}
        assert switched["warnings"] == ["provider 'other' has no API key"]
        assert calls == [(session_id, "other/big-model")]

        info_after = await harness.result("session/info", {"sessionId": session_id})
        assert info_after["warnings"] == ["provider 'other' has no API key"]


async def test_session_set_model_without_a_switcher_is_unsupported(
    tmp_path: Path,
) -> None:
    host = make_host(tmp_path)
    async with RpcHarness(host) as harness:
        error = await harness.error("session/set-model", {"model": "x/y"})
        assert error["code"] == -32004


async def test_extra_methods_are_merged(tmp_path: Path) -> None:
    async def ping(params: dict[str, Any]) -> dict[str, Any]:
        return {"pong": params.get("n", 0)}

    host = ServerHost(
        Scripted(tmp_path),
        manager=None,
        initial=SessionOptions(cwd=tmp_path),
        extra_methods={"custom/ping": ping},
    )
    async with RpcHarness(host) as harness:
        info = await harness.result("initialize", {})
        assert "custom/ping" in info["methods"]
        assert await harness.result("custom/ping", {"n": 3}) == {"pong": 3}


async def test_rebind_provider_swaps_running_sessions(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    runtime = host._sessions[host._default_id]  # type: ignore[index]
    runtime.agent.model = Model(id="fake-model", provider="fake")
    replacement = object()
    assert host.rebind_provider("fake", replacement) == [runtime.id]
    assert runtime.agent.provider is replacement
    assert host.rebind_provider("other", object()) == []
