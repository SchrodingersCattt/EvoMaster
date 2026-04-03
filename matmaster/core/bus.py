"""DEPRECATED: MessageBus -- scheduled for removal in Plan 03.

MessageBus is no longer used in the service execution path (replaced by
RunEventFanout). This stub exists only because matmaster/core/exp.py still
accepts ``bus: MessageBus`` in its signatures. Plan 03 removes the bus=
parameter from Exp, at which point this file is deleted.
"""

from __future__ import annotations

import asyncio

from matmaster.types.events import BusEvent


class MessageBus:
    """Async event bus (deprecated -- replaced by RunEventFanout).

    Retained as a stub for Exp.build_runtime(bus=...) compatibility.
    Will be physically deleted when Plan 03 removes the bus parameter.
    """

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: asyncio.Queue[BusEvent] = asyncio.Queue(maxsize=maxsize)

    async def emit(self, event: BusEvent) -> None:
        self._queue.put_nowait(event)

    def emit_nowait(self, event: BusEvent) -> None:
        self._queue.put_nowait(event)

    async def get(self, timeout: float | None = None) -> BusEvent:
        if timeout is None:
            return await self._queue.get()
        return await asyncio.wait_for(self._queue.get(), timeout)

    def get_nowait(self) -> BusEvent:
        return self._queue.get_nowait()

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    @property
    def empty(self) -> bool:
        return self._queue.empty()
