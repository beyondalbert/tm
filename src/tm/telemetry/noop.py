"""No-op telemetry adapter: the default when none is configured."""

from __future__ import annotations

from typing import Any

from tm.telemetry.contract import Span, TelemetryContext


class NoopTelemetry:
    """Discards every span and event. The safe default."""

    def start(self, span: Span, context: TelemetryContext) -> str:
        return ""

    def end(self, span_id: str, context: TelemetryContext, *, status: str = "ok") -> None:
        return None

    def event(self, name: str, attributes: dict[str, Any], context: TelemetryContext) -> None:
        return None


__all__ = ["NoopTelemetry"]
