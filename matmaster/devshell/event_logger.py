"""EventLogger -- JSONL event persistence for devshell."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from matmaster.types.events import (
    ResponseEvent,
    RunResultEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
)

logger = logging.getLogger(__name__)

# Event types to skip (by their Literal ``type`` value)
_SKIP_TYPES = {"assistant_state"}


class EventLogger:
    """Writes bus events to a JSONL file.

    Merges streaming ThoughtEvents / ResponseEvents (start/streaming/end) into
    a single record each. Skips assistant_state events.
    """

    def __init__(self, log_file: Path, *, run_id: str) -> None:
        self._log_file = log_file
        self._run_id = run_id
        self._fh: TextIO | None = None
        self._thought_buffer: dict[str, list[str]] = {}  # stream_id -> content parts
        self._thought_start_mono: dict[str, float] = (
            {}
        )  # stream_id -> perf_counter at thought start
        self._thought_start_ts: dict[str, str] = (
            {}
        )  # stream_id -> ISO ts at thought start
        self._response_buffer: dict[str, list[str]] = {}
        self._response_start_mono: dict[str, float] = {}
        self._response_start_ts: dict[str, str] = {}

    # ── public API ────────────────────────────────────────

    def log_event(self, event: Any) -> None:
        """Process a single bus event."""
        try:
            self._log_event_inner(event)
        except Exception:
            logger.warning("EventLogger failed to write event", exc_info=True)

    def set_run_id(self, run_id: str) -> None:
        """Update the run_id for subsequent records."""
        self._run_id = run_id

    def close(self) -> None:
        """Flush and close the log file."""
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    # ── internals ─────────────────────────────────────────

    def _ensure_open(self) -> TextIO:
        if self._fh is None:
            self._log_file.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self._log_file, "a", encoding="utf-8")
        return self._fh

    def _log_event_inner(self, event: Any) -> None:
        event_type = getattr(event, "type", None)
        if event_type in _SKIP_TYPES:
            return

        if isinstance(event, ThoughtEvent):
            self._handle_thought(event)
            return
        if isinstance(event, ResponseEvent):
            self._handle_response(event)
            return

        record = self._event_to_record(event)
        if record:
            self._write_record(record)

    def _handle_thought(self, event: ThoughtEvent) -> None:
        sid = event.stream_id or "default"

        if event.stream_state == "start":
            self._thought_buffer[sid] = []
            self._thought_start_mono[sid] = time.perf_counter()
            self._thought_start_ts[sid] = datetime.now(timezone.utc).isoformat()
        elif event.stream_state == "streaming":
            self._thought_buffer.setdefault(sid, []).append(event.content)
        elif event.stream_state == "end":
            parts = self._thought_buffer.pop(sid, [])
            content = "".join(parts)
            start_mono = self._thought_start_mono.pop(sid, None)
            start_ts = self._thought_start_ts.pop(sid, None)
            duration_ms: float | None = None
            if start_mono is not None:
                duration_ms = (time.perf_counter() - start_mono) * 1000.0
            if content:
                rec: dict[str, Any] = {
                    "type": "thought",
                    "content": content,
                }
                if start_ts is not None:
                    rec["ts_start"] = start_ts
                if duration_ms is not None:
                    rec["duration_ms"] = round(duration_ms, 3)
                self._write_record(rec)
        else:
            # Non-streaming thought (stream_state is None)
            if event.content:
                self._write_record(
                    {
                        "type": "thought",
                        "content": event.content,
                    }
                )

    def _handle_response(self, event: ResponseEvent) -> None:
        sid = event.stream_id or "default"

        if event.stream_state == "start":
            self._response_buffer[sid] = []
            self._response_start_mono[sid] = time.perf_counter()
            self._response_start_ts[sid] = datetime.now(timezone.utc).isoformat()
        elif event.stream_state == "streaming":
            self._response_buffer.setdefault(sid, []).append(event.content)
        elif event.stream_state == "end":
            parts = self._response_buffer.pop(sid, [])
            content = "".join(parts)
            start_mono = self._response_start_mono.pop(sid, None)
            start_ts = self._response_start_ts.pop(sid, None)
            duration_ms: float | None = None
            if start_mono is not None:
                duration_ms = (time.perf_counter() - start_mono) * 1000.0
            if content:
                rec: dict[str, Any] = {
                    "type": "response",
                    "content": content,
                }
                if start_ts is not None:
                    rec["ts_start"] = start_ts
                if duration_ms is not None:
                    rec["duration_ms"] = round(duration_ms, 3)
                self._write_record(rec)
        else:
            if event.content:
                self._write_record(
                    {
                        "type": "response",
                        "content": event.content,
                    }
                )

    def _event_to_record(self, event: Any) -> dict[str, Any] | None:
        if isinstance(event, ToolCallEvent):
            return {
                "type": "tool_call",
                "tool": event.tool_name,
                "call_id": event.call_id,
                "args": event.arguments,
            }
        if isinstance(event, ToolResultEvent):
            return {
                "type": "tool_result",
                "tool": event.tool_name,
                "call_id": event.call_id,
                "content": event.result,
            }
        if isinstance(event, RunResultEvent):
            return {
                "type": "run_result",
                "status": event.status,
                "reason": event.reason,
            }
        return None

    def _write_record(self, record: dict[str, Any]) -> None:
        record["ts"] = datetime.now(timezone.utc).isoformat()
        record["run_id"] = self._run_id
        fh = self._ensure_open()
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()
