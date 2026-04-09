"""RunEventFanout -- per-run async fanout owner for event dispatch.

Replaces the MessageBus + EventRouter transport with direct handler
dispatch. Created per run, owned by AgentRunService.

Dispatch order:
1. SSE handler (awaited, latency-sensitive)
2. Extra handlers in registration order (awaited)
3. Persistence handler (background asyncio.Task, strong-referenced)

Lifecycle: dispatch() during run, drain_and_close() in finally block.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from typing import Any, Protocol, runtime_checkable

from matmaster.types.events import BusEvent

logger = logging.getLogger(__name__)


# -- EventHandler Protocol --------------------------------------------


@runtime_checkable
class EventHandler(Protocol):
    """Protocol for event handlers consumed by RunEventFanout."""

    async def handle(self, event: BusEvent) -> None:
        """Process a single bus event."""
        ...


# -- RunEventFanout ---------------------------------------------------


class RunEventFanout:
    """Per-run async fanout owner for direct handler dispatch.

    SSE handler is always awaited first for low frontend latency.
    Persistence handler runs as a background task (asyncio.create_task)
    so it does not block the event stream.

    drain_and_close() must be called in the finally block to:
    1. Await all pending persistence tasks
    2. Call close() on all handlers (sync or async)
    """

    def __init__(
        self,
        *,
        sse_handler: Any,
        persistence_handler: Any,
        extra_handlers: list[Any] | None = None,
    ) -> None:
        self._sse = sse_handler
        self._persistence = persistence_handler
        self._extra_handlers: list[Any] = list(extra_handlers) if extra_handlers else []
        self._dispatch_state_lock = threading.Lock()
        self._dispatch_seq = 0
        self._pre_persistence_dispatches: set[int] = set()
        self._pending_persistence: set[asyncio.Task[None]] = set()
        self._pending_persistence_by_seq: dict[int, asyncio.Task[None]] = {}

    def add_handler(self, handler: Any) -> None:
        """Register a new extra handler for future dispatches.

        Mirrors EventRouter.add_handler() semantics: only affects
        future dispatch calls, not the current in-flight event.
        """
        self._extra_handlers = [*self._extra_handlers, handler]

    async def dispatch(self, event: BusEvent) -> None:
        """Dispatch an event from the event-loop thread."""
        seq = self._reserve_dispatch_seq()
        await self._dispatch_with_seq(seq, event)

    def dispatch_from_thread(self, loop: Any, event: BusEvent) -> None:
        """Reserve dispatch ordering before scheduling from another thread."""
        seq = self._reserve_dispatch_seq()

        def _start_dispatch() -> None:
            try:
                asyncio.create_task(self._dispatch_with_seq(seq, event))
            except Exception:
                self._discard_pre_persistence_dispatch(seq)
                raise

        try:
            loop.call_soon_threadsafe(_start_dispatch)
        except Exception:
            self._discard_pre_persistence_dispatch(seq)
            raise

    def _reserve_dispatch_seq(self) -> int:
        with self._dispatch_state_lock:
            seq = self._next_dispatch_seq()
            self._pre_persistence_dispatches.add(seq)
            return seq

    def _discard_pre_persistence_dispatch(self, seq: int) -> None:
        with self._dispatch_state_lock:
            self._pre_persistence_dispatches.discard(seq)

    def _capture_barrier_fence_seq(self) -> int:
        with self._dispatch_state_lock:
            return self._dispatch_seq

    def _has_pre_persistence_dispatch_at_or_before(self, fence_seq: int) -> bool:
        with self._dispatch_state_lock:
            pending = tuple(self._pre_persistence_dispatches)
        return any(seq <= fence_seq for seq in pending)

    async def _dispatch_with_seq(self, seq: int, event: BusEvent) -> None:
        """Dispatch event to all handlers.

        Order:
        1. SSE handler (await) -- latency-sensitive path
        2. Extra handlers (await each) -- in registration order
        3. Persistence handler (background task) -- non-blocking

        Per-handler exceptions are caught and logged. One failing
        handler does not prevent others from receiving the event.
        """
        try:
            # 1. SSE first -- latency-sensitive
            await self._safe_handle(self._sse, event)

            # 2. Extra handlers in registration order
            # Snapshot to ensure add_handler() during dispatch does not
            # affect the current event (same semantics as EventRouter).
            extra = self._extra_handlers
            for handler in extra:
                await self._safe_handle(handler, event)

            # 3. Persistence as background task
            self._spawn_persistence(seq, event)
        finally:
            self._discard_pre_persistence_dispatch(seq)

    def _next_dispatch_seq(self) -> int:
        self._dispatch_seq += 1
        return self._dispatch_seq

    def _spawn_persistence(self, seq: int, event: BusEvent) -> None:
        """Schedule persistence as a background task with strong reference."""
        task = asyncio.create_task(
            self._safe_handle(self._persistence, event),
            name="persist-event",
        )
        self._pending_persistence.add(task)
        self._pending_persistence_by_seq[seq] = task

        def _cleanup(done_task: asyncio.Task[None]) -> None:
            self._pending_persistence.discard(done_task)
            current = self._pending_persistence_by_seq.get(seq)
            if current is done_task:
                self._pending_persistence_by_seq.pop(seq, None)

        task.add_done_callback(_cleanup)

    async def flush_persistence_barrier(self) -> None:
        """Wait for dispatches started before the barrier to reach persistence."""
        fence_seq = self._capture_barrier_fence_seq()

        while self._has_pre_persistence_dispatch_at_or_before(fence_seq):
            await asyncio.sleep(0)

        pending = [
            task
            for seq, task in self._pending_persistence_by_seq.items()
            if seq <= fence_seq
        ]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)

    async def drain_and_close(self) -> None:
        """Drain pending persistence tasks and close all handlers.

        Must be called in the finally block of the run to ensure:
        1. All persistence tasks complete (no dropped events)
        2. WorkspaceHandler.close() waits for uploads to finish
        """
        # 1. Drain all pending persistence tasks
        await self.flush_persistence_barrier()

        # 2. Close all handlers
        all_handlers = [self._sse, *self._extra_handlers, self._persistence]
        for handler in all_handlers:
            if handler is None:
                continue
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

    async def _safe_handle(self, handler: Any, event: BusEvent) -> None:
        """Dispatch to a single handler with exception isolation."""
        try:
            await handler.handle(event)
        except Exception:
            logger.warning(
                "Handler %s raised for event type=%s",
                type(handler).__name__,
                getattr(event, "type", "?"),
                exc_info=True,
            )
