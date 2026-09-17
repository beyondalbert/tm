"""File-backed telemetry adapter: appends redacted spans/events as JSONL."""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

from tm.telemetry.contract import Span, TelemetryContext
from tm.telemetry.redact import redact

_counter = itertools.count(1)


class FileTelemetry:
    """Writes redacted spans and events to a JSONL file.

    Every record is passed through :func:`tm.telemetry.redact`, so the same
    content-safety guarantee the in-memory adapter has applies here. Failures to
    write never raise: telemetry must not break a run.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def _write(self, record: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def start(self, span: Span, context: TelemetryContext) -> str:
        span_id = f"span-{next(_counter)}"
        self._write(
            {
                "kind": "span_start",
                "span_id": span_id,
                "name": span.name,
                "attributes": redact(span.attributes),
                "trace_id": context.trace_id,
                "parent_id": context.parent_id,
            }
        )
        return span_id

    def end(self, span_id: str, context: TelemetryContext, *, status: str = "ok") -> None:
        self._write({"kind": "span_end", "span_id": span_id, "status": status})

    def event(self, name: str, attributes: dict[str, Any], context: TelemetryContext) -> None:
        self._write(
            {
                "kind": "event",
                "name": name,
                "attributes": redact(attributes),
                "trace_id": context.trace_id,
            }
        )


__all__ = ["FileTelemetry"]
