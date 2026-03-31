"""EventLogger -- JSONL event persistence for devshell."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from matmaster.types.events import (
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

    Merges streaming ThoughtEvents (start/streaming/end) into a single record.
    Skips assistant_state events.
    """

    def __init__(self, log_file: Path, *, run_id: str) -> None:
        self._log_file = log_file
        self._run_id = run_id
        self._fh: TextIO | None = None
        self._thought_buffer: dict[str, list[str]] = {}  # stream_id -> content parts

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

        record = self._event_to_record(event)
        if record:
            self._write_record(record)

    def _handle_thought(self, event: ThoughtEvent) -> None:
        sid = event.stream_id or "default"

        if event.stream_state == "start":
            self._thought_buffer[sid] = []
        elif event.stream_state == "streaming":
            self._thought_buffer.setdefault(sid, []).append(event.content)
        elif event.stream_state == "end":
            parts = self._thought_buffer.pop(sid, [])
            content = "".join(parts)
            if content:
                self._write_record(
                    {
                        "type": "thought",
                        "content": content,
                    }
                )
        elif event.stream_state == "complete":
            # Segment snapshot from on_segment_complete
            if event.content:
                self._write_record(
                    {
                        "type": "thought",
                        "content": event.content,
                        "complete": True,
                    }
                )
        else:
            # Non-streaming thought (stream_state is None)
            if event.content:
                self._write_record(
                    {
                        "type": "thought",
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
