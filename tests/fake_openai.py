"""A tiny OpenAI-compatible upstream for end-to-end tests.

Speaks just enough of ``POST /v1/chat/completions`` (SSE streaming) for TM's
OpenAI-compatible adapter to run a real turn, so integration tests exercise the
provider path instead of a scripted stream.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict

Plan = Dict[str, Any]
Responder = Callable[[Dict[str, Any]], Plan]


def _chunk(
    delta: dict[str, Any],
    *,
    finish: str | None = None,
    usage: dict[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "fake",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage is not None:
        payload["usage"] = usage
    return "data: " + json.dumps(payload) + "\n\n"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # keep test output clean
        return

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        body = json.loads(raw or b"{}")
        server: Any = self.server
        server.requests.append(body)
        plan = server.responder(body)

        frames: list[str] = [_chunk({"role": "assistant", "content": ""})]
        tool_call = plan.get("tool_call")
        if tool_call:
            frames.append(
                _chunk(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": tool_call.get("id", "call-1"),
                                "type": "function",
                                "function": {
                                    "name": tool_call["name"],
                                    "arguments": json.dumps(tool_call.get("arguments", {})),
                                },
                            }
                        ]
                    }
                )
            )
        if plan.get("text"):
            frames.append(_chunk({"content": plan["text"]}))
        frames.append(
            _chunk(
                {},
                finish=str(plan.get("finish", "stop")),
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )
        )
        frames.append("data: [DONE]\n\n")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for frame in frames:
            self.wfile.write(frame.encode("utf-8"))
        self.wfile.flush()
        self.close_connection = True


class FakeOpenAI:
    """Serve ``/v1/chat/completions`` from a responder callback."""

    def __init__(self, responder: Responder) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.responder = responder  # type: ignore[attr-defined]
        self._server.requests = []  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{str(host)}:{port}/v1"

    @property
    def requests(self) -> list[dict[str, Any]]:
        return list(self._server.requests)  # type: ignore[attr-defined]

    def __enter__(self) -> FakeOpenAI:
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


__all__ = ["FakeOpenAI"]
