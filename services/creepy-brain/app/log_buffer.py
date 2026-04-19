"""In-memory per-workflow log ring buffer.

Used to surface live step logs to the UI without DB overhead.
Logs are captured via context vars set by the engine runner before each step.
"""

from __future__ import annotations

import contextvars
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Context vars set by the runner before executing each step.
workflow_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "workflow_id", default=None
)
step_name_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "step_name", default=None
)


@dataclass
class LogEntry:
    timestamp: str
    level: str
    message: str
    step: str | None = None


class WorkflowLogBuffer:
    """Thread-safe in-memory ring buffer, keyed by workflow_id."""

    def __init__(self, maxlen: int = 500) -> None:
        self._maxlen = maxlen
        self._buffers: dict[str, deque[LogEntry]] = {}
        self._lock = threading.Lock()

    def append(self, workflow_id: str, entry: LogEntry) -> None:
        with self._lock:
            if workflow_id not in self._buffers:
                self._buffers[workflow_id] = deque(maxlen=self._maxlen)
            self._buffers[workflow_id].append(entry)

    def get(self, workflow_id: str) -> list[LogEntry]:
        with self._lock:
            return list(self._buffers.get(workflow_id, []))

    def clear(self, workflow_id: str) -> None:
        with self._lock:
            self._buffers.pop(workflow_id, None)


# Singleton buffer shared by logging handler and structlog processor.
log_buffer = WorkflowLogBuffer()


class WorkflowLogHandler(logging.Handler):
    """Stdlib logging handler that writes to the workflow log buffer."""

    def emit(self, record: logging.LogRecord) -> None:
        wid = workflow_id_var.get()
        if not wid:
            return
        try:
            ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
            msg = self.format(record)
            entry = LogEntry(
                timestamp=ts,
                level=record.levelname,
                message=msg,
                step=step_name_var.get(),
            )
            log_buffer.append(wid, entry)
        except Exception:  # noqa: BLE001
            pass  # never let log capture crash the app


def structlog_capture_processor(
    logger: object, method: str, event_dict: dict[str, object]
) -> dict[str, object]:
    """Structlog processor that copies events into the workflow log buffer."""
    wid = workflow_id_var.get()
    if wid:
        try:
            ts = str(event_dict.get("timestamp", datetime.now(timezone.utc).isoformat()))
            level = str(event_dict.get("level", method)).upper()
            msg = str(event_dict.get("event", ""))
            entry = LogEntry(
                timestamp=ts,
                level=level,
                message=msg,
                step=step_name_var.get(),
            )
            log_buffer.append(wid, entry)
        except Exception:  # noqa: BLE001
            pass
    return event_dict
