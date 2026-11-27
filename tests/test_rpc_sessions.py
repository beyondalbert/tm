from __future__ import annotations

from pathlib import Path

import pytest

from tests.rpc_fixtures import Scripted, finished, make_host
from tests.test_rpc_server import RpcHarness
from tm.safety import Journal, record_file_change


async def test_session_create_reports_options(tmp_path: Path) -> None:
    sub = tmp_path / "pkg"
    sub.mkdir()
    host = make_host(tmp_path)
    async with RpcHarness(host) as harness:
        await harness.result("initialize")
        info = await harness.result(
            "session/create", {"cwd": str(sub), "access": "read-only"}
        )
        assert info["cwd"] == str(sub)
        assert info["access"] == "read-only"
        assert info["persisted"] is True
        assert info["running"] is False


async def test_session_create_validates_access_and_cwd(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    async with RpcHarness(host) as harness:
        bad_access = await harness.error("session/create", {"access": "root"})
        assert "access must be one of" in bad_access["message"]

        missing = await harness.error(
            "session/create", {"cwd": str(tmp_path / "nope")}
        )
        assert "cwd is not a directory" in missing["message"]


async def test_session_limit(tmp_path: Path) -> None:
    host = make_host(tmp_path, max_sessions=1)
    async with RpcHarness(host) as harness:
        error = await harness.error("session/create", {})
        assert error["code"] == -32006
        assert error["data"] == {"maxSessions": 1}


async def test_relative_cwd_is_resolved_against_the_default_session(
    tmp_path: Path,
) -> None:
    sub = tmp_path / "pkg"
    sub.mkdir()
    host = make_host(tmp_path)
    async with RpcHarness(host) as harness:
        info = await harness.result("session/create", {"cwd": "pkg"})
        assert info["cwd"] == str(sub)


async def test_session_list_marks_open_sessions(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    async with RpcHarness(host) as harness:
        first = await harness.result("initialize")
        created = await harness.result("session/create", {})
        listing = await harness.result("session/list")
        ids = {item["sessionId"]: item for item in listing["sessions"]}
        assert first["session"]["sessionId"] in ids
        assert ids[created["sessionId"]]["open"] is True
        assert ids[first["session"]["sessionId"]]["open"] is True
        assert all(item["messageCount"] == 0 for item in listing["sessions"])


async def test_session_open_only_accepts_the_sessions_directory(
    tmp_path: Path,
) -> None:
    host = make_host(tmp_path)
    outside = tmp_path / "other.jsonl"
    outside.write_text("{}\n", encoding="utf-8")
    async with RpcHarness(host) as harness:
        error = await harness.error("session/open", {"path": str(outside)})
        assert "inside the sessions directory" in error["message"]

        created = await harness.result("session/create", {})
        path = created["path"]
        assert await harness.result("session/close", {"sessionId": created["sessionId"]})
        reopened = await harness.result("session/open", {"path": path})
        assert reopened["sessionId"] == created["sessionId"]
        assert reopened["persisted"] is True


async def test_session_fork_copies_messages(tmp_path: Path) -> None:
    host = make_host(tmp_path, [[finished("hello")], [finished("child")]])
    async with RpcHarness(host) as harness:
        await harness.result("prompt", {"content": "hi"})
        await harness.wait_notification("turn/end")
        source = await harness.result("session/info")
        assert source["messageCount"] == 2

        forked = await harness.result("session/fork", {})
        assert forked["sessionId"] != source["sessionId"]
        assert forked["messageCount"] == 2


async def test_session_undo_restores_a_file(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    journal = Journal(tmp_path / "changes" / "changes.jsonl")
    target = tmp_path / "notes.txt"
    target.write_text("new\n", encoding="utf-8")
    async with RpcHarness(host) as harness:
        info = await harness.result("initialize")
        session_id = info["session"]["sessionId"]
        record_file_change(
            journal,
            session=session_id,
            tool="edit",
            path=target,
            existed=True,
            content=b"old\n",
            summary="edited notes.txt",
        )

        result = await harness.result("session/undo", {"count": 1})
        assert result["undone"] and "restored" in result["undone"][0]
        assert target.read_text(encoding="utf-8") == "old\n"
        assert [change.status for change in journal.entries()] == ["reverted"]

        again = await harness.result("session/undo", {"count": 1})
        assert again["undone"] == []


async def test_session_close_removes_the_session(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    async with RpcHarness(host) as harness:
        created = await harness.result("session/create", {})
        session_id = created["sessionId"]
        assert await harness.result("session/close", {"sessionId": session_id}) == {
            "closed": session_id
        }
        error = await harness.error("session/info", {"sessionId": session_id})
        assert error["code"] == -32007


async def test_memory_sessions_have_no_persistence(tmp_path: Path) -> None:
    host = make_host(tmp_path, manager=False)
    async with RpcHarness(host) as harness:
        info = await harness.result("session/info")
        assert info["persisted"] is False
        assert info["path"] is None

        assert (await harness.error("session/open", {"path": "x"}))["code"] == -32005
        assert await harness.result("session/list") == {"sessions": []}


async def test_session_undo_without_a_journal_is_unsupported(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    runtime = host._sessions[host._default_id]  # type: ignore[index]
    runtime.journal = None
    async with RpcHarness(host) as harness:
        error = await harness.error("session/undo", {})
        assert error["code"] == -32004


@pytest.mark.parametrize("count", [0, -2])
async def test_session_undo_rejects_bad_counts(tmp_path: Path, count: int) -> None:
    host = make_host(tmp_path)
    async with RpcHarness(host) as harness:
        error = await harness.error("session/undo", {"count": count})
        assert error["code"] == -32602


async def test_session_delete_removes_the_file(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    async with RpcHarness(host) as harness:
        created = await harness.result("session/create", {})
        session_id = created["sessionId"]
        path = Path(created["path"])
        assert path.is_file()

        deleted = await harness.result("session/delete", {"sessionId": session_id})
        assert deleted["deleted"] == session_id
        assert not path.exists()
        assert (await harness.error("session/info", {"sessionId": session_id}))["code"] == -32007


async def test_session_delete_rejects_memory_sessions(tmp_path: Path) -> None:
    host = make_host(tmp_path, manager=False)
    async with RpcHarness(host) as harness:
        error = await harness.error("session/delete", {})
        assert error["code"] == -32005


async def test_session_delete_refuses_a_running_turn(tmp_path: Path) -> None:
    from tests.rpc_fixtures import hanging

    host = make_host(tmp_path, streams=[hanging])
    async with RpcHarness(host) as harness:
        await harness.result("prompt", {"content": "hang"})
        error = await harness.error("session/delete", {})
        assert error["code"] == -32001
        await harness.result("cancel", {})


async def test_session_create_passes_host_instructions(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    builder = host._builder
    assert isinstance(builder, Scripted)
    async with RpcHarness(host) as harness:
        await harness.result(
            "session/create", {"instructions": "Always answer in Chinese."}
        )
    assert builder.options[-1].instructions == "Always answer in Chinese."


def test_host_instructions_are_appended_to_the_system_prompt() -> None:
    from tm.cli.main import Resources, _system_prompt
    from tm.config import Settings

    prompt = _system_prompt(
        Settings(system_prompt="be terse"),
        Resources(),
        True,
        instructions="You are the assistant inside Acme.",
    )
    assert "be terse" in prompt
    assert "Product instructions:" in prompt
    assert "You are the assistant inside Acme." in prompt
