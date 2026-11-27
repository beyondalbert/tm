from __future__ import annotations

from pathlib import Path

from tests.rpc_fixtures import finished, make_host
from tests.test_rpc_server import RpcHarness
from tm.rpc.mcp import MCP_PROTOCOL_VERSION, McpServer


async def test_initialize_and_tools_list(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    async with RpcHarness(McpServer(host)) as harness:
        info = await harness.result("initialize", {"protocolVersion": MCP_PROTOCOL_VERSION})
        assert info["protocolVersion"] == MCP_PROTOCOL_VERSION
        assert info["capabilities"]["tools"] == {"listChanged": False}
        assert info["serverInfo"]["name"] == "tm"

        tools = await harness.result("tools/list")
        names = [tool["name"] for tool in tools["tools"]]
        assert names == ["tm_run", "tm_session_info"]
        schema = tools["tools"][0]["inputSchema"]
        assert schema["required"] == ["prompt"]


async def test_ping(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    async with RpcHarness(McpServer(host)) as harness:
        assert await harness.result("ping") == {}


async def test_tools_call_runs_a_turn(tmp_path: Path) -> None:
    host = make_host(tmp_path, [[finished("all tests pass")]])
    async with RpcHarness(McpServer(host)) as harness:
        result = await harness.result(
            "tools/call", {"name": "tm_run", "arguments": {"prompt": "run the tests"}}
        )
        assert result["isError"] is False
        assert result["content"] == [{"type": "text", "text": "all tests pass"}]


async def test_tools_call_uses_a_session_per_cwd(tmp_path: Path) -> None:
    sub = tmp_path / "pkg"
    sub.mkdir()
    host = make_host(
        tmp_path, [[finished("default")], [finished("in pkg"), finished("again")]]
    )
    async with RpcHarness(McpServer(host)) as harness:
        result = await harness.result(
            "tools/call",
            {"name": "tm_run", "arguments": {"prompt": "hi", "cwd": str(sub)}},
        )
        assert result["content"][0]["text"] == "in pkg"

        again = await harness.result(
            "tools/call",
            {"name": "tm_run", "arguments": {"prompt": "hi", "cwd": str(sub)}},
        )
        assert again["content"][0]["text"] == "again"


async def test_session_info_tool(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    async with RpcHarness(McpServer(host)) as harness:
        result = await harness.result(
            "tools/call", {"name": "tm_session_info", "arguments": {}}
        )
        text = result["content"][0]["text"]
        assert "session " in text
        assert "fake-model" in text


async def test_unknown_tool_and_missing_arguments(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    async with RpcHarness(McpServer(host)) as harness:
        assert (await harness.error("tools/call", {"name": "nope"}))["code"] == -32601
        error = await harness.error("tools/call", {"name": "tm_run", "arguments": {}})
        assert "prompt must be a non-empty string" in error["message"]


async def test_failed_turn_is_an_mcp_error(tmp_path: Path) -> None:
    host = make_host(tmp_path, streams=None)
    runtime = host._sessions[host._default_id]  # type: ignore[index]
    runtime.agent.last_stop_reason = "error"
    async with RpcHarness(McpServer(host)) as harness:
        # Force the turn to end as failed by making prompt raise.
        async def boom(_content: str) -> None:
            raise RuntimeError("provider down")

        runtime.agent.prompt = boom  # type: ignore[assignment]
        result = await harness.result(
            "tools/call", {"name": "tm_run", "arguments": {"prompt": "hi"}}
        )
        assert result["isError"] is True
        assert "provider down" in result["content"][0]["text"]


async def test_idempotency_key_replays_a_tool_call(tmp_path: Path) -> None:
    host = make_host(tmp_path, [[finished("first")]])
    async with RpcHarness(McpServer(host)) as harness:
        first = await harness.result(
            "tools/call",
            {"name": "tm_run", "arguments": {"prompt": "hi", "idempotencyKey": "k"}},
        )
        again = await harness.result(
            "tools/call",
            {"name": "tm_run", "arguments": {"prompt": "hi", "idempotencyKey": "k"}},
        )
        # The scripted agent has one reply: a second run would fail, so the same
        # text proves the stored outcome was replayed.
        assert first["content"][0]["text"] == "first"
        assert again["content"][0]["text"] == "first"
        assert again["isError"] is False
