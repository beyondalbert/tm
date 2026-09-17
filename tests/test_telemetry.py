"""Conformance tests for the telemetry contract and the redaction policy."""

from __future__ import annotations

from tm.telemetry import (
    REDACTED_KEYS,
    MemoryTelemetry,
    NoopTelemetry,
    Span,
    Telemetry,
    TelemetryContext,
    redact,
)


def test_adapters_satisfy_the_contract() -> None:
    assert isinstance(MemoryTelemetry(), Telemetry)
    assert isinstance(NoopTelemetry(), Telemetry)


def test_redact_drops_content_keys() -> None:
    out = redact({"prompt": "secret plan", "tool": "shell", "turn": 2})
    assert out["prompt"] == "<redacted>"
    assert out["tool"] == "shell"
    assert out["turn"] == 2


def test_redact_is_case_and_substring_insensitive() -> None:
    out = redact({"ToolArgs": {"command": "rm -rf /"}, "API_KEY": "sk-xyz"})
    assert out["ToolArgs"] == "<redacted>"
    assert out["API_KEY"] == "<redacted>"


def test_redact_drops_structured_values() -> None:
    out = redact({"nested": {"a": 1}, "count": 5, "tags": ["a", "b"]})
    assert "nested" not in out
    assert out["count"] == 5
    assert out["tags"] == ["a", "b"]


def test_redact_truncates_long_scalar_lists() -> None:
    out = redact({"ids": list(range(100))})
    assert len(out["ids"]) == 32


def test_memory_adapter_never_stores_content() -> None:
    telemetry = MemoryTelemetry()
    ctx = TelemetryContext(trace_id="t1")
    span_id = telemetry.start(Span("tm.tool", {"tool": "write", "args": "file body"}), ctx)
    telemetry.end(span_id, ctx, status="ok")
    telemetry.event("tm.error", {"error": "boom", "message": "user text"}, ctx)

    assert len(telemetry.spans) == 1
    recorded = telemetry.spans[0]
    assert recorded.attributes["tool"] == "write"
    assert recorded.attributes["args"] == "<redacted>"
    assert recorded.status == "ok"
    assert telemetry.events[0].attributes["message"] == "<redacted>"


def test_noop_adapter_is_silent() -> None:
    telemetry = NoopTelemetry()
    ctx = TelemetryContext(trace_id="t1")
    assert telemetry.start(Span("tm.operation"), ctx) == ""
    telemetry.end("", ctx)
    telemetry.event("x", {}, ctx)


def test_redacted_keys_cover_secrets() -> None:
    for key in ("api_key", "token", "password", "authorization", "secret"):
        assert key in REDACTED_KEYS
