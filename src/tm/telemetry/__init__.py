"""Vendor-neutral telemetry contracts for TM.

Adapted from Pi's ``pi-telemetry`` package idea: a slim, dependency-free contract
that the agent core emits against, plus a reference in-memory adapter and a
no-op adapter. Keeping it separate from the runtime means the recording policy
(what is safe to report) lives in one place and is enforced by construction.

Hard rule: telemetry never carries prompts, message content, tool arguments,
tool output, file contents, or credentials. Spans carry structural metadata and
counts only. :func:`redact` is the single choke point that drops anything else,
and the conformance tests assert it.
"""

from tm.telemetry.contract import (
    SPAN_ERROR,
    SPAN_OPERATION,
    SPAN_TOOL,
    SPAN_TURN,
    Span,
    Telemetry,
    TelemetryContext,
)
from tm.telemetry.file import FileTelemetry
from tm.telemetry.memory import MemoryTelemetry
from tm.telemetry.noop import NoopTelemetry
from tm.telemetry.redact import REDACTED_KEYS, redact

__all__ = [
    "REDACTED_KEYS",
    "SPAN_ERROR",
    "SPAN_OPERATION",
    "SPAN_TOOL",
    "SPAN_TURN",
    "FileTelemetry",
    "MemoryTelemetry",
    "NoopTelemetry",
    "Span",
    "Telemetry",
    "TelemetryContext",
    "redact",
]
