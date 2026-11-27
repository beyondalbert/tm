from __future__ import annotations

import asyncio
from typing import Any, Callable

from tm.permissions.actions import Action, ActionKind
from tm.rpc.approver import RpcApprover

SHELL = Action(kind=ActionKind.SHELL, target="pytest -q", tool_name="shell")


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def __call__(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


async def _wait_for(predicate: Callable[[], bool], timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition was not met in time")
        await asyncio.sleep(0.005)


async def test_allow_round_trip() -> None:
    send = _Recorder()
    approver = RpcApprover(send, timeout_ms=500)
    task: asyncio.Future[Any] = asyncio.ensure_future(
        approver.request(SHELL, "no matching allow rule")
    )

    await _wait_for(lambda: bool(send.sent))
    request = send.sent[0]
    assert request["method"] == "permission/request"
    params = request["params"]
    assert params["action"] == {"kind": "shell", "target": "pytest -q", "tool": "shell"}
    assert params["reason"] == "no matching allow rule"
    assert params["options"] == ["allow", "allow_scope", "deny"]

    assert approver.resolve(params["requestId"], "allow") is True
    outcome = await task
    assert outcome.allowed is True
    assert outcome.remember is False
    assert outcome.source == "host"


async def test_allow_scope_remembers() -> None:
    send = _Recorder()
    approver = RpcApprover(send, timeout_ms=500)
    task: asyncio.Future[Any] = asyncio.ensure_future(approver.request(SHELL, "x"))

    await _wait_for(lambda: bool(send.sent))
    request_id = send.sent[0]["params"]["requestId"]
    assert approver.resolve(request_id, "allow_scope") is True
    outcome = await task
    assert outcome.allowed is True
    assert outcome.remember is True


async def test_deny() -> None:
    send = _Recorder()
    approver = RpcApprover(send, timeout_ms=500)
    task: asyncio.Future[Any] = asyncio.ensure_future(approver.request(SHELL, "x"))

    await _wait_for(lambda: bool(send.sent))
    request_id = send.sent[0]["params"]["requestId"]
    assert approver.resolve(request_id, "deny") is True
    outcome = await task
    assert outcome.allowed is False
    assert outcome.source == "host"


async def test_timeout_denies_and_reports() -> None:
    send = _Recorder()
    approver = RpcApprover(send, timeout_ms=10)
    outcome = await approver.request(SHELL, "x")

    assert outcome.allowed is False
    assert outcome.source == "host-timeout"
    assert [payload["method"] for payload in send.sent] == [
        "permission/request",
        "permission/expired",
    ]
    assert approver.pending() == []


async def test_unknown_request_id_is_not_silently_accepted() -> None:
    send = _Recorder()
    approver = RpcApprover(send, timeout_ms=500)
    assert approver.resolve("p-999", "allow") is False


async def test_invalid_decision_is_rejected() -> None:
    send = _Recorder()
    approver = RpcApprover(send, timeout_ms=500)
    task: asyncio.Future[Any] = asyncio.ensure_future(approver.request(SHELL, "x"))

    await _wait_for(lambda: bool(send.sent))
    request_id = send.sent[0]["params"]["requestId"]
    assert approver.resolve(request_id, "always") is False
    approver.resolve(request_id, "deny")
    await task


async def test_second_answer_is_rejected() -> None:
    send = _Recorder()
    approver = RpcApprover(send, timeout_ms=500)
    task: asyncio.Future[Any] = asyncio.ensure_future(approver.request(SHELL, "x"))

    await _wait_for(lambda: bool(send.sent))
    request_id = send.sent[0]["params"]["requestId"]
    assert approver.resolve(request_id, "deny") is True
    await task
    assert approver.resolve(request_id, "allow") is False
