"""DevEventObserver -- SimpleQueue-backed local event observer for DevShell.

Replaces the MessageBus dependency in DevShell with a thread-safe local
observer that converts hook callbacks into structured event objects for
EventLogger consumption.

Components:
- DevEventObserver: SimpleQueue-backed event collector (thread-safe)
- DevEventHook: BaseHook subclass that converts kernel hook callbacks
  into ThoughtEvent/ResponseEvent/ToolCallEvent/ToolResultEvent objects

Usage pattern:
    observer = DevEventObserver()
    # ... wire observer.hook into runtime spec hooks ...
    result = runner.run(task, event_observer=observer)
    for event in observer.drain():
        event_logger.log_event(event)
"""

from __future__ import annotations

import uuid
from queue import Empty, SimpleQueue
from typing import Any

from matmaster.core.hooks import BaseHook, HookAction
from matmaster.tools.tool_result import ToolResult
from matmaster.types.events import (
    ContextCompactionEvent,
    ResponseEvent,
    RunResultEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from matmaster.types.messages import StreamChunk, ToolCallData

_SOURCE = "devshell"


class DevEventObserver:
    """Thread-safe event collector using queue.SimpleQueue.

    DevShell runs kernel.run_stream() in a worker thread while the main thread
    polls for events. SimpleQueue is the correct stdlib choice for
    cross-thread handoff (asyncio.Queue is not thread-safe).
    """

    def __init__(self) -> None:
        self._queue: SimpleQueue[Any] = SimpleQueue()
        self.hook: DevEventHook = DevEventHook(self)

    def emit(self, event: Any) -> None:
        """Put an event into the queue (thread-safe, non-blocking)."""
        self._queue.put(event)

    def get_nowait(self) -> Any:
        """Get an event without blocking. Raises queue.Empty if empty."""
        return self._queue.get_nowait()

    def drain(self) -> list[Any]:
        """Drain all pending events into a list."""
        events: list[Any] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except Empty:
                break
        return events

    def make_event_sink(self) -> Any:
        """Return an async callable suitable for ContextCompactor event_sink."""

        async def _sink(event: Any) -> None:
            self.emit(event)

        return _sink


class DevEventHook(BaseHook):
    """Hook that converts kernel callbacks into event objects for DevEventObserver.

    Converts:
    - on_segment_complete(thought) -> ThoughtEvent
    - on_segment_complete(response) -> ResponseEvent
    - pre_tool_call -> ToolCallEvent
    - post_tool_call -> ToolResultEvent
    """

    def __init__(self, observer: DevEventObserver) -> None:
        self._observer = observer

    async def on_segment_complete(
        self, segment_type: str, content: str, stream_id: str | None
    ) -> None:
        """Convert completed thought/response segments into events."""
        if segment_type == "thought":
            self._observer.emit(
                ThoughtEvent(
                    source=_SOURCE,
                    content=content,
                    stream_state="complete",
                    stream_id=stream_id,
                )
            )
        elif segment_type == "response":
            self._observer.emit(
                ResponseEvent(
                    source=_SOURCE,
                    content=content,
                    stream_state="complete",
                    stream_id=stream_id,
                )
            )

    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        """Emit ToolCallEvent before tool execution."""
        self._observer.emit(
            ToolCallEvent(
                source=_SOURCE,
                call_id=tool_call.id,
                tool_name=tool_call.name,
                arguments=tool_call.arguments,
            )
        )
        return HookAction.CONTINUE

    async def post_tool_call(
        self, tool_call: ToolCallData, result: ToolResult
    ) -> None:
        """Emit ToolResultEvent after tool execution."""
        self._observer.emit(
            ToolResultEvent(
                source=_SOURCE,
                call_id=tool_call.id,
                tool_name=tool_call.name,
                result=result.content,
                is_error=result.status == "error",
            )
        )
