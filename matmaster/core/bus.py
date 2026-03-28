"""Async event bus backed by asyncio.Queue.

MessageBus is the core transport for BusEvent objects between
the agent kernel (producer) and EventRouter handlers (consumer).
"""

from __future__ import annotations

import asyncio
from typing import Optional

from matmaster.types.events import BusEvent


class MessageBus:
    """Async event bus.

    Agent kernel calls await emit() to publish BusEvent.
    EventRouter consumes via await get() in an async task.
    Based on asyncio.Queue, safe within a single event loop.

    For cross-thread callers (service layer), emit_nowait() uses
    loop.call_soon_threadsafe to schedule put_nowait on the correct
    event loop, avoiding the asyncio.Queue thread-safety issue
    flagged by Gemini + Codex reviews.
    """

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: asyncio.Queue[BusEvent] = asyncio.Queue(maxsize=maxsize)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture event loop reference for thread-safe emit_nowait.

        Called by EventRouter.start() after the router loop is running.
        """
        self._loop = loop

    async def emit(self, event: BusEvent) -> None:
        """Emit event (non-blocking for unbounded queue).

        Must be called from within the event loop (e.g. from hooks/compactor).
        """
        self._queue.put_nowait(event)

    def emit_nowait(self, event: BusEvent) -> None:
        """Thread-safe sync emit for cross-thread callers (service layer).

        Uses call_soon_threadsafe to schedule put_nowait on the bus's
        event loop. Falls back to direct put_nowait if no loop is set
        (e.g. during testing or before router starts).

        Phase 19 service layer async migration will remove this method.
        """
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._queue.put_nowait, event)
        else:
            self._queue.put_nowait(event)

    async def get(self, timeout: float | None = None) -> BusEvent:
        """Consume next event with optional timeout.

        Raises asyncio.TimeoutError if timeout expires.
        """
        if timeout is None:
            return await self._queue.get()
        return await asyncio.wait_for(self._queue.get(), timeout)

    def get_nowait(self) -> BusEvent:
        """Non-blocking consume. Raises asyncio.QueueEmpty when empty."""
        return self._queue.get_nowait()

    @property
    def pending(self) -> int:
        """Pending event count (approximate)."""
        return self._queue.qsize()

    @property
    def empty(self) -> bool:
        return self._queue.empty()
