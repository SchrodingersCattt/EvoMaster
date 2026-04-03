"""DevStreamHook -- real-time terminal output for devshell REPL."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

_MAX_RESULT_LEN = 1000


class DevStreamHook:
    """Format generator events for terminal display."""

    def __init__(
        self,
        output: TextIO | None = None,
        verbose: bool = False,
    ) -> None:
        self._out = output or sys.stdout
        self._verbose = verbose

    # ── Event-based dispatch (run_stream path) ────────────

    def on_event(self, event: Any) -> None:
        """Dispatch a BusEvent to the appropriate terminal output handler."""
        from matmaster.types.events import (
            ResponseEvent,
            ThoughtEvent,
            ToolCallEvent,
            ToolResultEvent,
        )

        if isinstance(event, (ThoughtEvent, ResponseEvent)):
            self._handle_stream_event(event)
        elif isinstance(event, ToolCallEvent):
            self._handle_tool_call_event(event)
        elif isinstance(event, ToolResultEvent):
            self._handle_tool_result_event(event)

    def _handle_stream_event(self, event: Any) -> None:
        if event.stream_state == "start":
            return
        if event.stream_state == "end":
            self._out.write("\n")
            self._out.flush()
            return
        content = getattr(event, "content", "")
        if content:
            self._out.write(content)
            self._out.flush()

    def _handle_tool_call_event(self, event: Any) -> None:
        args_str = json.dumps(event.arguments, ensure_ascii=False, indent=2)
        self._out.write(f"\n\U0001f4ce tool_call: {event.tool_name}\n")
        for line in args_str.split("\n"):
            self._out.write(f"   {line}\n")
        self._out.flush()

    def _handle_tool_result_event(self, event: Any) -> None:
        is_error = event.status == "error"
        prefix = "\u274c tool_error:" if is_error else "\u2705 tool_result:"
        display = str(event.result) if event.result else ""
        if len(display) > _MAX_RESULT_LEN:
            display = display[:_MAX_RESULT_LEN] + "..."
        self._out.write(f"\n{prefix} {display}\n\n")
        self._out.flush()
