"""Async event bus backed by asyncio.Queue.

MessageBus is the core transport for BusEvent objects between
the agent kernel (producer) and EventRouter handlers (consumer).
"""

from __future__ import annotations

import asyncio

from matmaster.types.events import BusEvent


class MessageBus:
    """Async event bus.

    Agent kernel and hooks call ``await emit()`` to publish BusEvent
    from within the event loop.  EventRouter consumes via ``await get()``
    in an async task.

    ``emit_nowait()`` provides a synchronous interface for callers within
    the same event loop thread (e.g. sync callbacks).
    """

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: asyncio.Queue[BusEvent] = asyncio.Queue(maxsize=maxsize)

    async def emit(self, event: BusEvent) -> None:
        """Emit event (non-blocking for unbounded queue).

        Must be called from within the event loop.
        """
        self._queue.put_nowait(event)

    def emit_nowait(self, event: BusEvent) -> None:
        """Synchronous emit for callers within the event loop thread.

        Safe to call from sync code running on the same thread as the
        event loop (e.g. sync callbacks invoked during await chains).
        """
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
