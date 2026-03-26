"""EventRouter -- single-consumer multi-handler event dispatch.

Components:
- EventHandler: Protocol for handler interface
- EventRouter: background thread consumer + multi-handler dispatch

Lifecycle: EventRouter is bound to a single run (per D-15).
Created in run_agent_sync(), stopped in finally block.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Protocol, runtime_checkable

from matmaster.core.bus import MessageBus
from matmaster.types.events import BusEvent

logger = logging.getLogger(__name__)


# ── EventHandler Protocol ────────────────────────────────


@runtime_checkable
class EventHandler(Protocol):
    """Protocol for event handlers consumed by EventRouter."""

    def handle(self, event: BusEvent) -> None:  # type: ignore[arg-type]
        """Process a single bus event."""
        ...


# ── EventRouter ──────────────────────────────────────────


class EventRouter:
    """Background thread consumer that dispatches events to handlers.

    Single-consumer pattern: consumes from MessageBus in a daemon thread,
    dispatches each event to all registered handlers.

    Lifecycle bound to a single run (D-15):
    - start(): spawns daemon thread
    - stop(drain_timeout): joins consumer, drains queue, closes handlers
    """

    def __init__(self, bus: MessageBus, handlers: list[EventHandler]) -> None:
        self._bus = bus
        self._handlers = handlers
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Spawn daemon thread running the consume loop."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._consume_loop, daemon=True, name="event-router"
        )
        self._thread.start()

    def add_handler(self, handler: EventHandler) -> None:
        """Register a new handler for future dispatches."""
        self._handlers = [*self._handlers, handler]

    def stop(self, drain_timeout: float = 2.0) -> None:
        """Signal stop, wait for consumer, drain remaining events, close handlers.

        Args:
            drain_timeout: max seconds to spend draining queued events
                after the consumer thread exits.
        """
        self._stop_event.set()

        if self._thread is not None:
            self._thread.join()
            self._thread = None

        # Drain remaining events from bus within deadline
        deadline = time.monotonic() + drain_timeout
        while time.monotonic() < deadline:
            try:
                event = self._bus.get_nowait()
                self._dispatch(event)
            except queue.Empty:
                break

        self._close_handlers()

    def _consume_loop(self) -> None:
        """Main consume loop -- runs in background thread."""
        while not self._stop_event.is_set():
            try:
                event = self._bus.get(timeout=0.1)
                self._dispatch(event)
            except queue.Empty:
                continue

    def _dispatch(self, event: BusEvent) -> None:  # type: ignore[arg-type]
        """Dispatch event to all handlers, catching exceptions."""
        handlers = self._handlers
        for handler in handlers:
            try:
                handler.handle(event)
            except Exception:
                logger.warning(
                    "Handler %s raised exception for event type=%s",
                    type(handler).__name__,
                    getattr(event, "type", "?"),
                    exc_info=True,
                )

    def _close_handlers(self) -> None:
        """Flush handler-owned resources after dispatch stops."""
        for handler in self._handlers:
            close = getattr(handler, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except Exception:
                logger.warning(
                    "Handler %s raised during close()",
                    type(handler).__name__,
                    exc_info=True,
                )
