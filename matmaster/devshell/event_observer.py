"""DevEventObserver -- SimpleQueue-backed local event observer for DevShell.

Replaces the MessageBus dependency in DevShell with a thread-safe local
observer that forwards generator events to EventLogger consumption.

Components:
- DevEventObserver: SimpleQueue-backed event collector (thread-safe)
- DevEventHook: lightweight adapter with on_event() for compatibility

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


class DevEventHook:
    """Compatibility adapter that forwards generator events to the observer."""

    def __init__(self, observer: DevEventObserver) -> None:
        self._observer = observer

    def on_event(self, event: Any) -> None:
        self._observer.emit(event)
