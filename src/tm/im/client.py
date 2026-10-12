"""REST client for tm_im: writes, discovery, and gap backfill.

Uses only the standard library. Business failures arrive as HTTP 200 with a
non-zero ``code`` and become :class:`ImApiError`; only authentication, rate
limiting, and server faults use non-200 status codes.
"""

from __future__ import annotations

import asyncio
import json
import random
import urllib.error
import urllib.parse
import urllib.request
import uuid

_RETRYABLE_SERVER = {50000, 50001, 50002, 50003, 50004, 50006}
_AUTH_ERRORS = {40101, 40102, 40103, 40104, 40105, 40106}


class ImApiError(Exception):
    def __init__(
        self,
        code: int,
        message: str,
        *,
        retryable: bool = False,
        retry_after: float | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after
        self.http_status = http_status


def classify(code: int, message: str, retry_after: float | None = None) -> ImApiError:
    if code == 42901 or code == 42904:
        return ImApiError(code, message, retryable=True, retry_after=retry_after)
    if code in _RETRYABLE_SERVER:
        return ImApiError(code, message, retryable=True)
    if code in _AUTH_ERRORS:
        return ImApiError(code, message)
    return ImApiError(code, message)


class ImClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        device_id: str = "tm",
        timeout: float = 15.0,
        max_retries: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.device_id = device_id
        self.timeout = timeout
        self.max_retries = max_retries

    # -- low-level --------------------------------------------------------
    def _sync_request(
        self, method: str, path: str, body: dict | None, params: dict | None
    ) -> dict:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        data = (
            json.dumps(body, ensure_ascii=False).encode("utf-8")
            if body is not None
            else None
        )
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.api_key}")
        request.add_header("X-TM-Device-Id", self.device_id)
        if data is not None:
            request.add_header("Content-Type", "application/json; charset=utf-8")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            code = int(payload.get("code", -1))
            if code != 0:
                raise classify(code, str(payload.get("message", "")))
            return payload.get("data") or {}
        except urllib.error.HTTPError as error:
            retry_after = error.headers.get("Retry-After") if error.headers else None
            parsed: dict = {}
            try:
                parsed = json.loads(error.read().decode("utf-8", "ignore"))
            except (ValueError, OSError):
                parsed = {}
            code = int(parsed.get("code", -(error.code)))
            message = str(parsed.get("message") or f"HTTP {error.code}")
            exc = classify(code, message, float(retry_after) if retry_after else None)
            exc.http_status = error.code
            if error.code == 429 and not exc.retryable:
                exc.retryable = True
            raise exc from error
        except urllib.error.URLError as error:
            raise ImApiError(
                -1, f"network error: {error.reason}", retryable=True
            ) from error

    async def request(
        self, method: str, path: str, body: dict | None = None, params: dict | None = None
    ) -> dict:
        backoff = 1.0
        attempt = 0
        while True:
            try:
                return await asyncio.to_thread(
                    self._sync_request, method, path, body, params
                )
            except ImApiError as error:
                if not error.retryable or attempt >= self.max_retries:
                    raise
                delay = (
                    error.retry_after
                    if error.retry_after is not None
                    else min(30.0, backoff) * (1 + random.uniform(-0.3, 0.3))
                )
                attempt += 1
                backoff *= 2
                await asyncio.sleep(delay)

    # -- endpoints --------------------------------------------------------
    async def me(self) -> dict:
        return await self.request("GET", "/me")

    async def conversations(self, limit: int = 50) -> list[dict]:
        items: list[dict] = []
        cursor: str | None = None
        while True:
            params: dict = {"limit": limit}
            if cursor:
                params["cursor"] = cursor
            data = await self.request("GET", "/conversations", params=params)
            items.extend(data.get("items", []))
            cursor = data.get("next_cursor")
            if not cursor:
                return items

    async def conversation(self, conv_id: int) -> dict:
        return await self.request("GET", f"/conversations/{conv_id}")

    async def messages(
        self, conv_id: int, *, since_seq: int | None = None, cursor: str | None = None, limit: int = 200
    ) -> dict:
        params: dict = {"limit": limit}
        if since_seq is not None:
            params["since_seq"] = since_seq
        if cursor:
            params["cursor"] = cursor
        return await self.request("GET", f"/conversations/{conv_id}/messages", params=params)

    async def send_text(
        self,
        conv_id: int,
        text: str,
        *,
        client_msg_id: str | None = None,
        reply_to: int | None = None,
    ) -> dict:
        body: dict = {
            "msg_type": "TEXT",
            "client_msg_id": client_msg_id or f"tm-{uuid.uuid4().hex}",
            "content": {"text": text},
        }
        if reply_to:
            body["reply_to"] = reply_to
        return await self.request("POST", f"/conversations/{conv_id}/messages", body=body)

    async def mark_read(self, conv_id: int, last_read_seq: int) -> dict:
        return await self.request(
            "POST",
            f"/conversations/{conv_id}/read",
            body={"last_read_seq": last_read_seq},
        )

    async def friends_requests(
        self, direction: str = "incoming", status: str = "pending"
    ) -> list[dict]:
        data = await self.request(
            "GET",
            "/friends/requests",
            params={"direction": direction, "status": status},
        )
        return data.get("items", [])

    async def accept_friend(self, request_id: int) -> dict:
        return await self.request("POST", f"/friends/requests/{request_id}/accept")


__all__ = ["ImApiError", "ImClient", "classify"]
