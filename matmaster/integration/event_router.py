"""EventRouter -- single-consumer multi-handler event dispatch.

Components:
- EventHandler: Protocol for handler interface
- EventRouter: asyncio.Task consumer + multi-handler dispatch

Lifecycle: EventRouter is bound to a single run (per D-15).
Created in run_agent_sync(), stopped in finally block.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Protocol, runtime_checkable

from matmaster.core.bus import MessageBus
from matmaster.types.events import BusEvent

logger = logging.getLogger(__name__)


# -- EventHandler Protocol ----------------------------------------


@runtime_checkable
class EventHandler(Protocol):
    """Protocol for event handlers consumed by EventRouter."""

    async def handle(self, event: BusEvent) -> None:
        """Process a single bus event."""
        ...


# -- EventRouter --------------------------------------------------


class EventRouter:
    """Async task consumer that dispatches events to handlers.

    Single-consumer pattern: consumes from MessageBus in an asyncio.Task,
    dispatches each event to all registered handlers.

    Lifecycle bound to a single run (D-15):
    - start(): spawns asyncio.Task
    - stop(drain_timeout): cancels consumer, drains queue, closes handlers
    """

    def __init__(self, bus: MessageBus, handlers: list[EventHandler]) -> None:
        self._bus = bus
        self._handlers = handlers
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Spawn asyncio.Task running the consume loop."""
        self._stop_event.clear()
        self._bus.set_loop(asyncio.get_running_loop())
        self._task = asyncio.create_task(self._consume_loop(), name="event-router")

    def add_handler(self, handler: EventHandler) -> None:
        """Register a new handler for future dispatches."""
        self._handlers = [*self._handlers, handler]

    async def stop(self, drain_timeout: float = 2.0) -> None:
        """Signal stop, wait for consumer, drain remaining events, close handlers.

        Args:
            drain_timeout: max seconds to spend draining queued events
                after the consumer task exits.
        """
        self._stop_event.set()

        if self._task is not None:
            await self._task
            self._task = None

        # Drain remaining events from bus within deadline
        loop = asyncio.get_running_loop()
        deadline = loop.time() + drain_timeout
        while loop.time() < deadline:
            try:
                event = self._bus.get_nowait()
                await self._dispatch(event)
            except asyncio.QueueEmpty:
                break

        await self._close_handlers()

    async def _consume_loop(self) -> None:
        """Main consume loop -- runs as asyncio.Task."""
        while not self._stop_event.is_set():
            try:
                event = await self._bus.get(timeout=0.1)
                await self._dispatch(event)
            except asyncio.TimeoutError:
                continue

    async def _dispatch(self, event: BusEvent) -> None:
        """Dispatch event to all handlers, catching exceptions."""
        handlers = self._handlers
        for handler in handlers:
            try:
                await handler.handle(event)
            except Exception:
                logger.warning(
                    "Handler %s raised exception for event type=%s",
                    type(handler).__name__,
                    getattr(event, "type", "?"),
                    exc_info=True,
                )

    async def _close_handlers(self) -> None:
        """Flush handler-owned resources after dispatch stops.

        Uses inspect.isawaitable(result) pattern to correctly handle:
        sync close, async def close, AsyncMock close, partial-wrapped close.
        """
        for handler in self._handlers:
            close = getattr(handler, "close", None)
            if not callable(close):
                continue
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.warning(
                    "Handler %s raised during close()",
                    type(handler).__name__,
                    exc_info=True,
                )
