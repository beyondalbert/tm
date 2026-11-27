"""End-to-end: a real TM process, a real provider adapter, a real tool call.

This is the closest test to what a third-party product does on day one: launch
``tm serve`` against an OpenAI-compatible endpoint, prompt it, and read the
result. No scripted stream anywhere in this file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fake_openai import FakeOpenAI
from tm.sdk import TmClient, TmError


def _write_config(config: Path, base_url: str) -> None:
    config.mkdir(parents=True, exist_ok=True)
    (config / "settings.toml").write_text(
        'provider = "fake"\nmodel = "fake-model"\nenv_probe = false\nauto_compact = false\n',
        encoding="utf-8",
    )
    (config / "models.toml").write_text(
        '[providers.fake]\nname = "Fake"\napi = "openai-completions"\n'
        f'base_url = "{base_url}"\ndefault_model = "fake-model"\n\n'
        '[[providers.fake.models]]\nid = "fake-model"\ncontext_window = 32768\n'
        "max_tokens = 4096\n",
        encoding="utf-8",
    )


async def test_real_turn_with_a_tool_call(tmp_path: Path) -> None:
    target = tmp_path / "work" / "app.py"
    target.parent.mkdir()
    config = tmp_path / "config"

    def responder(body: dict) -> dict:
        messages = body.get("messages") or []
        saw_tool_result = any(
            isinstance(message, dict) and message.get("role") == "tool"
            for message in messages
        )
        if not saw_tool_result and body.get("tools"):
            return {
                "tool_call": {
                    "id": "call-1",
                    "name": "write",
                    "arguments": {
                        "path": str(target),
                        "content": "print('hi')\n",
                    },
                },
                "finish": "tool_calls",
            }
        return {"text": "all done", "finish": "stop"}

    with FakeOpenAI(responder) as upstream:
        _write_config(config, upstream.base_url)
        client = await TmClient.connect(
            env={"TM_CONFIG_DIR": str(config)},
            extra_args=["--mode", "policy"],
        )
        try:
            info = await client.initialize("third-party")
            assert info["capabilities"]["streaming"] is True

            session = await client.create_session(cwd=str(target.parent))
            turn = await session.prompt("create app.py")
            result = await turn.wait(timeout=30)

            assert result.status == "done", result.raw
            assert result.text == "all done"
            assert result.usage is not None
            # Two model calls, 10 prompt tokens each.
            assert result.usage["input"] == 20
            assert result.usage["output"] == 10
            assert result.stop_reason == "stop"
            assert target.read_text(encoding="utf-8") == "print('hi')\n"

            paths = [change.path for change in result.changed_files]
            assert paths == [str(target)]
            change = result.changed_files[0]
            assert change.added == 1
            assert change.removed == 0
            assert change.diff and "print('hi')" in change.diff

            kinds = [event.get("type") for event in result.events]
            assert "tool_execution_start" in kinds
            assert "tool_execution_end" in kinds

            # The upstream saw two round trips: the tool call, then the answer.
            assert len(upstream.requests) == 2
            assert upstream.requests[0]["tools"], "tools must be advertised"
        finally:
            await client.close()

    assert (config / "audit.jsonl").exists()
    audit = (config / "audit.jsonl").read_text(encoding="utf-8")
    assert "file_write" in audit


async def test_real_turn_reports_provider_errors(tmp_path: Path) -> None:
    config = tmp_path / "config"
    _write_config(config, "http://127.0.0.1:9/v1")  # nothing listening
    (config / "settings.toml").write_text(
        "provider = 'fake'\nmodel = 'fake-model'\nenv_probe = false\n"
        "auto_compact = false\nmax_retries = 1\n",
        encoding="utf-8",
    )

    client = await TmClient.connect(
        env={"TM_CONFIG_DIR": str(config)},
        extra_args=["--mode", "policy"],
    )
    try:
        session = await client.create_session(cwd=str(tmp_path))
        result = await (await session.prompt("hello")).wait(timeout=60)
        assert result.status == "failed"
        assert result.error is None  # the agent reports errors in the message
        assert result.raw["stopReason"] == "error"
    finally:
        await client.close()


async def test_tool_calls_respect_read_only_access(tmp_path: Path) -> None:
    target = tmp_path / "app.py"

    def responder(body: dict) -> dict:
        messages = body.get("messages") or []
        if not any(
            isinstance(message, dict) and message.get("role") == "tool"
            for message in messages
        ):
            names = [
                tool["function"]["name"]
                for tool in body.get("tools") or []
                if isinstance(tool, dict)
            ]
            if "write" in names:
                return {
                    "tool_call": {
                        "id": "call-1",
                        "name": "write",
                        "arguments": {"path": str(target), "content": "x\n"},
                    },
                    "finish": "tool_calls",
                }
            return {"text": "cannot write", "finish": "stop"}
        return {"text": "wrote it", "finish": "stop"}

    with FakeOpenAI(responder) as upstream:
        config = tmp_path / "config"
        _write_config(config, upstream.base_url)
        client = await TmClient.connect(
            env={"TM_CONFIG_DIR": str(config)},
            extra_args=["--mode", "policy"],
        )
        try:
            session = await client.create_session(
                cwd=str(tmp_path), access="read-only"
            )
            result = await (await session.prompt("create a file")).wait(timeout=30)
            assert result.text == "cannot write"
            assert not target.exists()
            with pytest.raises(TmError):
                await session.prompt("")
        finally:
            await client.close()
