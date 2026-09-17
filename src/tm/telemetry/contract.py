"""Telemetry contract: the schema the agent core emits against.

No third-party SDK is imported here. Adapters implement :class:`Telemetry` and
translate spans into whatever backend they like. The contract is deliberately
narrow so the "what may be reported" rule stays auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# Span names. Keep this list small and stable; adding one is a contract change.
SPAN_OPERATION = "tm.operation"
SPAN_TURN = "tm.turn"
SPAN_TOOL = "tm.tool"
SPAN_ERROR = "tm.error"


@dataclass
class Span:
    """One structural unit of work.

    ``attributes`` must contain only scalars or small lists of scalars and must
    never contain user content. :func:`tm.telemetry.redact` enforces this before
    any adapter sees a span.
    """

    name: str
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class TelemetryContext:
    """Process-local parentage for one call; never durable data.

    ``trace_id`` groups spans across a logical request; ``parent_id`` links a
    child span to its parent. A context is passed explicitly down the call chain
    so concurrent calls never share ambient state.
    """

    trace_id: str
    parent_id: str | None = None


@runtime_checkable
class Telemetry(Protocol):
    """The contract adapters implement."""

    def start(self, span: Span, context: TelemetryContext) -> str:
        """Record a span start; returns a span id for later ``end``."""
        ...

    def end(self, span_id: str, context: TelemetryContext, *, status: str = "ok") -> None:
        """Record a span end with a terminal status."""
        ...

    def event(self, name: str, attributes: dict[str, Any], context: TelemetryContext) -> None:
        """Record a point-in-time event."""
        ...


__all__ = [
    "SPAN_ERROR",
    "SPAN_OPERATION",
    "SPAN_TOOL",
    "SPAN_TURN",
    "Span",
    "Telemetry",
    "TelemetryContext",
]
