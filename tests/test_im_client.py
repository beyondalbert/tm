from __future__ import annotations

import pytest

from tm.im.client import ImApiError, ImClient, classify


async def test_send_text_builds_request(monkeypatch) -> None:
    client = ImClient("http://x/v1", "k")
    captured: dict = {}

    def fake(method: str, path: str, body, params) -> dict:
        captured.update(method=method, path=path, body=body, params=params)
        return {"seq": 7}

    monkeypatch.setattr(client, "_sync_request", fake)
    data = await client.send_text(1001, "hi", client_msg_id="c-1")

    assert data == {"seq": 7}
    assert captured["method"] == "POST"
    assert captured["path"] == "/conversations/1001/messages"
    assert captured["body"]["msg_type"] == "TEXT"
    assert captured["body"]["content"] == {"text": "hi"}
    assert captured["body"]["client_msg_id"] == "c-1"


async def test_retries_retryable_then_succeeds(monkeypatch) -> None:
    client = ImClient("http://x/v1", "k")
    calls = {"n": 0}

    def fake(method: str, path: str, body, params) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ImApiError(42901, "rate", retryable=True, retry_after=0.0)
        return {"ok": True}

    monkeypatch.setattr(client, "_sync_request", fake)
    assert await client.request("GET", "/me") == {"ok": True}
    assert calls["n"] == 2


async def test_non_retryable_raises(monkeypatch) -> None:
    client = ImClient("http://x/v1", "k")

    def fake(method: str, path: str, body, params) -> dict:
        raise ImApiError(40003, "not friends")

    monkeypatch.setattr(client, "_sync_request", fake)
    with pytest.raises(ImApiError) as info:
        await client.request("POST", "/conversations/1/messages")
    assert info.value.code == 40003


async def test_gives_up_after_max_retries(monkeypatch) -> None:
    client = ImClient("http://x/v1", "k", max_retries=2)

    def fake(method: str, path: str, body, params) -> dict:
        raise ImApiError(50000, "boom", retryable=True, retry_after=0.0)

    monkeypatch.setattr(client, "_sync_request", fake)
    with pytest.raises(ImApiError):
        await client.request("GET", "/me")


def test_classify() -> None:
    assert classify(50000, "x").retryable is True
    assert classify(42901, "x", retry_after=2.0).retry_after == 2.0
    assert classify(40003, "x").retryable is False
    assert classify(40103, "x").retryable is False
