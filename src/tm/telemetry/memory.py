"""In-memory telemetry adapter: a reference implementation and test sink."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

from tm.telemetry.contract import Span, TelemetryContext
from tm.telemetry.redact import redact

_counter = itertools.count(1)


@dataclass
class RecordedSpan:
    span_id: str
    name: str
    attributes: dict[str, Any]
    trace_id: str
    parent_id: str | None
    status: str = "unset"


@dataclass
class RecordedEvent:
    name: str
    attributes: dict[str, Any]
    trace_id: str


@dataclass
class MemoryTelemetry:
    """Collects redacted spans and events in memory. Useful in tests."""

    spans: list[RecordedSpan] = field(default_factory=list)
    events: list[RecordedEvent] = field(default_factory=list)

    def start(self, span: Span, context: TelemetryContext) -> str:
        span_id = f"span-{next(_counter)}"
        self.spans.append(
            RecordedSpan(
                span_id=span_id,
                name=span.name,
                attributes=redact(span.attributes),
                trace_id=context.trace_id,
                parent_id=context.parent_id,
            )
        )
        return span_id

    def end(self, span_id: str, context: TelemetryContext, *, status: str = "ok") -> None:
        for span in self.spans:
            if span.span_id == span_id:
                span.status = status
                return

    def event(self, name: str, attributes: dict[str, Any], context: TelemetryContext) -> None:
        self.events.append(
            RecordedEvent(name=name, attributes=redact(attributes), trace_id=context.trace_id)
        )


__all__ = ["MemoryTelemetry", "RecordedEvent", "RecordedSpan"]
