"""Tests for RunEventFanout -- per-run async fanout owner.

Covers:
- dispatch() awaits SSE handler first, then extra handlers, then schedules
  persistence as background task
- add_handler() only delivers future events
- Handler exception isolation: one failing handler does not crash others
- drain_and_close() awaits pending persistence tasks and calls handler close()
- drain_and_close() handles both sync and async close() methods
- Error during persistence does not block SSE delivery
"""

from __future__ import annotations

import asyncio
import threading
import time

from matmaster.types.events import (
    BusEvent,
    RunResultEvent,
    ThoughtEvent,
)

# -- Helpers ----------------------------------------------------------


class _CollectorHandler:
    """Handler that records events in order."""

    def __init__(self) -> None:
        self.received: list[BusEvent] = []

    async def handle(self, event: BusEvent) -> None:
        self.received.append(event)


class _FailingHandler:
    """Handler that always raises."""

    async def handle(self, event: BusEvent) -> None:
        raise RuntimeError("handler boom")


class _SyncCloser:
    """Handler with a sync close() that records call."""

    def __init__(self) -> None:
        self.closed = False

    async def handle(self, event: BusEvent) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _AsyncCloser:
    """Handler with an async close() that records call."""

    def __init__(self) -> None:
        self.closed = False

    async def handle(self, event: BusEvent) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


class _SlowPersistence:
    """Handler that sleeps briefly to simulate slow persistence."""

    def __init__(self) -> None:
        self.received: list[BusEvent] = []
        self.completed: list[BusEvent] = []

    async def handle(self, event: BusEvent) -> None:
        self.received.append(event)
        await asyncio.sleep(0.05)
        self.completed.append(event)


class _BlockingPersistence:
    """Handler that blocks until released, for barrier tests."""

    def __init__(self) -> None:
        self.received: list[BusEvent] = []
        self.completed: list[BusEvent] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def handle(self, event: BusEvent) -> None:
        self.received.append(event)
        self.started.set()
        await self.release.wait()
        self.completed.append(event)


class _BlockingHandler:
    """Handler that blocks until released, for dispatch window tests."""

    def __init__(self) -> None:
        self.received: list[BusEvent] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def handle(self, event: BusEvent) -> None:
        self.received.append(event)
        self.started.set()
        await self.release.wait()


class _QueuedThreadsafeLoop:
    """Minimal loop stub that queues thread-safe callbacks until released."""

    def __init__(self) -> None:
        self.callbacks: list[tuple[object, tuple[object, ...]]] = []

    def call_soon_threadsafe(self, callback, *args) -> None:
        self.callbacks.append((callback, args))

    def run_next(self) -> None:
        callback, args = self.callbacks.pop(0)
        callback(*args)


class _NoopThreadsafeLoop:
    """Loop stub that accepts thread-safe scheduling without running callbacks."""

    def call_soon_threadsafe(self, callback, *args) -> None:
        pass


class _SlowFalseSeq:
    """Comparable set entry that slows barrier iteration for race reproduction."""

    def __init__(self, value: int, started: threading.Event) -> None:
        self._value = value
        self._started = started

    def __hash__(self) -> int:
        return hash(self._value)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _SlowFalseSeq) and other._value == self._value

    def __le__(self, other: object) -> bool:
        self._started.set()
        time.sleep(0.001)
        return False


class _OrderTracker:
    """Tracks dispatch ordering across handlers via shared list."""

    def __init__(self, name: str, shared_log: list[str]) -> None:
        self._name = name
        self._log = shared_log

    async def handle(self, event: BusEvent) -> None:
        self._log.append(self._name)


# -- Tests ------------------------------------------------------------


class TestRunEventFanoutDispatch:
    """dispatch() routes events to handlers in the correct order."""

    async def test_dispatch_calls_sse_handler_first(self) -> None:
        """SSE handler is awaited before any other handler."""
        from matmaster.integration.fanout import RunEventFanout

        order_log: list[str] = []
        sse = _OrderTracker("sse", order_log)
        persistence = _OrderTracker("persistence", order_log)

        fanout = RunEventFanout(
            sse_handler=sse,
            persistence_handler=persistence,
        )

        event = ThoughtEvent(source="Agent", content="hi")
        await fanout.dispatch(event)

        # SSE must appear before persistence scheduling
        assert order_log[0] == "sse"

        await fanout.drain_and_close()

    async def test_dispatch_delivers_to_extra_handlers_in_order(self) -> None:
        """Extra handlers receive events in registration order after SSE."""
        from matmaster.integration.fanout import RunEventFanout

        order_log: list[str] = []
        sse = _OrderTracker("sse", order_log)
        extra1 = _OrderTracker("extra1", order_log)
        extra2 = _OrderTracker("extra2", order_log)
        persistence = _SlowPersistence()

        fanout = RunEventFanout(
            sse_handler=sse,
            persistence_handler=persistence,
            extra_handlers=[extra1, extra2],
        )

        await fanout.dispatch(ThoughtEvent(source="Agent", content="hi"))
        # SSE first, then extra1, then extra2
        assert order_log == ["sse", "extra1", "extra2"]

        await fanout.drain_and_close()

    async def test_persistence_is_background_task(self) -> None:
        """Persistence handler runs as a background task, not blocking dispatch."""
        from matmaster.integration.fanout import RunEventFanout

        slow_persistence = _SlowPersistence()
        sse = _CollectorHandler()

        fanout = RunEventFanout(
            sse_handler=sse,
            persistence_handler=slow_persistence,
        )

        event = ThoughtEvent(source="Agent", content="hi")
        await fanout.dispatch(event)

        # After dispatch returns, persistence may not have completed yet
        # but should have been scheduled
        assert len(slow_persistence.received) <= 1
        assert len(sse.received) == 1

        await fanout.drain_and_close()
        # After drain, persistence must have completed
        assert len(slow_persistence.completed) == 1


class TestRunEventFanoutAddHandler:
    """add_handler() only affects future dispatches."""

    async def test_add_handler_delivers_only_future_events(self) -> None:
        """A handler added after dispatch only receives events emitted after registration."""
        from matmaster.integration.fanout import RunEventFanout

        sse = _CollectorHandler()
        persistence = _CollectorHandler()
        late = _CollectorHandler()

        fanout = RunEventFanout(
            sse_handler=sse,
            persistence_handler=persistence,
        )

        event1 = RunResultEvent(source="Agent", reason="before")
        await fanout.dispatch(event1)

        fanout.add_handler(late)

        event2 = RunResultEvent(source="Agent", reason="after")
        await fanout.dispatch(event2)

        await fanout.drain_and_close()

        assert len(late.received) == 1
        assert late.received[0].reason == "after"


class TestRunEventFanoutErrorIsolation:
    """Handler exceptions do not crash other handlers."""

    async def test_sse_exception_does_not_block_persistence(self) -> None:
        """If SSE handler raises, persistence still gets the event."""
        from matmaster.integration.fanout import RunEventFanout

        persistence = _CollectorHandler()

        fanout = RunEventFanout(
            sse_handler=_FailingHandler(),
            persistence_handler=persistence,
        )

        await fanout.dispatch(ThoughtEvent(source="Agent", content="hi"))
        await fanout.drain_and_close()

        assert len(persistence.received) == 1

    async def test_extra_handler_exception_does_not_block_others(self) -> None:
        """If an extra handler raises, the remaining extra handlers still run."""
        from matmaster.integration.fanout import RunEventFanout

        sse = _CollectorHandler()
        persistence = _CollectorHandler()
        good = _CollectorHandler()

        fanout = RunEventFanout(
            sse_handler=sse,
            persistence_handler=persistence,
            extra_handlers=[_FailingHandler(), good],
        )

        await fanout.dispatch(ThoughtEvent(source="Agent", content="hi"))
        await fanout.drain_and_close()

        assert len(good.received) == 1
        assert len(persistence.received) == 1

    async def test_persistence_exception_is_silently_caught(self) -> None:
        """Persistence handler exception does not propagate to caller."""
        from matmaster.integration.fanout import RunEventFanout

        sse = _CollectorHandler()

        fanout = RunEventFanout(
            sse_handler=sse,
            persistence_handler=_FailingHandler(),
        )

        # Should not raise
        await fanout.dispatch(ThoughtEvent(source="Agent", content="hi"))
        await fanout.drain_and_close()

        assert len(sse.received) == 1


class TestRunEventFanoutDrainAndClose:
    """drain_and_close() awaits pending tasks and calls handler close()."""

    async def test_drain_awaits_all_pending_persistence(self) -> None:
        """drain_and_close() waits for all scheduled persistence tasks to complete."""
        from matmaster.integration.fanout import RunEventFanout

        slow = _SlowPersistence()
        sse = _CollectorHandler()

        fanout = RunEventFanout(
            sse_handler=sse,
            persistence_handler=slow,
        )

        # Dispatch several events
        for i in range(5):
            await fanout.dispatch(ThoughtEvent(source="Agent", content=f"msg-{i}"))

        await fanout.drain_and_close()
        assert len(slow.completed) == 5

    async def test_close_calls_sync_closer(self) -> None:
        """drain_and_close() calls sync close() on handlers."""
        from matmaster.integration.fanout import RunEventFanout

        closer = _SyncCloser()
        persistence = _CollectorHandler()

        fanout = RunEventFanout(
            sse_handler=closer,
            persistence_handler=persistence,
        )

        await fanout.drain_and_close()
        assert closer.closed is True

    async def test_close_calls_async_closer(self) -> None:
        """drain_and_close() awaits async close() on handlers."""
        from matmaster.integration.fanout import RunEventFanout

        closer = _AsyncCloser()
        persistence = _CollectorHandler()

        fanout = RunEventFanout(
            sse_handler=closer,
            persistence_handler=persistence,
        )

        await fanout.drain_and_close()
        assert closer.closed is True

    async def test_close_handles_extra_handlers_and_persistence(self) -> None:
        """drain_and_close() calls close() on SSE, extra handlers, and persistence."""
        from matmaster.integration.fanout import RunEventFanout

        sse_closer = _SyncCloser()
        persistence_closer = _AsyncCloser()
        extra_closer = _SyncCloser()

        fanout = RunEventFanout(
            sse_handler=sse_closer,
            persistence_handler=persistence_closer,
            extra_handlers=[extra_closer],
        )

        await fanout.drain_and_close()
        assert sse_closer.closed is True
        assert extra_closer.closed is True
        assert persistence_closer.closed is True

    async def test_close_exception_does_not_prevent_other_closes(self) -> None:
        """If one handler's close() raises, others still get closed."""
        from matmaster.integration.fanout import RunEventFanout

        class _RaisingCloser:
            async def handle(self, event: BusEvent) -> None:
                pass

            def close(self) -> None:
                raise RuntimeError("close boom")

        good_closer = _SyncCloser()
        persistence = _CollectorHandler()

        fanout = RunEventFanout(
            sse_handler=_RaisingCloser(),
            persistence_handler=persistence,
            extra_handlers=[good_closer],
        )

        await fanout.drain_and_close()
        assert good_closer.closed is True


class TestRunEventFanoutPersistenceBarrier:
    """flush_persistence_barrier() waits pending persistence without closing fanout."""

    async def test_flush_persistence_barrier_waits_pending_tasks(self) -> None:
        """Barrier waits current persistence tasks, then fanout can keep dispatching."""
        from matmaster.integration.fanout import RunEventFanout

        persistence = _BlockingPersistence()
        sse = _CollectorHandler()

        fanout = RunEventFanout(
            sse_handler=sse,
            persistence_handler=persistence,
        )

        first_event = RunResultEvent(source="Agent", reason="before-barrier")
        await fanout.dispatch(first_event)
        await persistence.started.wait()

        barrier_task = asyncio.create_task(fanout.flush_persistence_barrier())
        await asyncio.sleep(0)
        assert barrier_task.done() is False

        persistence.release.set()
        await barrier_task

        second_event = RunResultEvent(source="Agent", reason="after-barrier")
        await fanout.dispatch(second_event)
        await fanout.drain_and_close()

        assert [event.reason for event in persistence.completed] == [
            "before-barrier",
            "after-barrier",
        ]
        assert [event.reason for event in sse.received] == [
            "before-barrier",
            "after-barrier",
        ]

    async def test_flush_barrier_is_noop_when_no_pending_tasks(self) -> None:
        """Barrier returns immediately when there is no pending persistence work."""
        from matmaster.integration.fanout import RunEventFanout

        persistence = _CollectorHandler()
        sse = _CollectorHandler()

        fanout = RunEventFanout(
            sse_handler=sse,
            persistence_handler=persistence,
        )

        await fanout.flush_persistence_barrier()

        event = ThoughtEvent(source="Agent", content="still-open")
        await fanout.dispatch(event)
        await fanout.drain_and_close()

        assert len(sse.received) == 1
        assert len(persistence.received) == 1

    async def test_flush_barrier_waits_dispatches_started_before_spawn(self) -> None:
        """Barrier must wait for pre-existing dispatches stuck before persistence spawn."""
        from matmaster.integration.fanout import RunEventFanout

        sse = _BlockingHandler()
        persistence = _BlockingPersistence()

        fanout = RunEventFanout(
            sse_handler=sse,
            persistence_handler=persistence,
        )

        first_event = RunResultEvent(source="Agent", reason="before-spawn-window")
        dispatch_task = asyncio.create_task(fanout.dispatch(first_event))
        await sse.started.wait()

        barrier_task = asyncio.create_task(fanout.flush_persistence_barrier())
        await asyncio.sleep(0)
        assert barrier_task.done() is False

        sse.release.set()
        await persistence.started.wait()
        await asyncio.sleep(0)
        assert barrier_task.done() is False

        persistence.release.set()
        await barrier_task
        await dispatch_task

        second_event = RunResultEvent(source="Agent", reason="after-barrier")
        await fanout.dispatch(second_event)
        await fanout.drain_and_close()

        assert [event.reason for event in sse.received] == [
            "before-spawn-window",
            "after-barrier",
        ]
        assert [event.reason for event in persistence.completed] == [
            "before-spawn-window",
            "after-barrier",
        ]

    async def test_flush_barrier_waits_thread_bridge_dispatch_reserved_before_start(
        self,
    ) -> None:
        """Barrier must see thread-bridge dispatches queued before loop callback starts."""
        from matmaster.integration.fanout import RunEventFanout

        queued_loop = _QueuedThreadsafeLoop()
        sse = _BlockingHandler()
        persistence = _BlockingPersistence()

        fanout = RunEventFanout(
            sse_handler=sse,
            persistence_handler=persistence,
        )

        first_event = RunResultEvent(source="Agent", reason="queued-thread-bridge")
        fanout.dispatch_from_thread(queued_loop, first_event)

        barrier_task = asyncio.create_task(fanout.flush_persistence_barrier())
        await asyncio.sleep(0)
        assert barrier_task.done() is False

        queued_loop.run_next()
        await sse.started.wait()
        await asyncio.sleep(0)
        assert barrier_task.done() is False

        sse.release.set()
        await persistence.started.wait()
        await asyncio.sleep(0)
        assert barrier_task.done() is False

        persistence.release.set()
        await barrier_task

        second_event = RunResultEvent(source="Agent", reason="after-barrier")
        await fanout.dispatch(second_event)
        await fanout.drain_and_close()

        assert [event.reason for event in sse.received] == [
            "queued-thread-bridge",
            "after-barrier",
        ]
        assert [event.reason for event in persistence.completed] == [
            "queued-thread-bridge",
            "after-barrier",
        ]

    async def test_flush_barrier_avoids_set_size_error_during_thread_reserve(self) -> None:
        """Barrier should not crash when thread bridge reserves while barrier checks state."""
        from matmaster.integration.fanout import RunEventFanout

        iteration_started = threading.Event()
        mutation_done = threading.Event()
        noop_loop = _NoopThreadsafeLoop()

        fanout = RunEventFanout(
            sse_handler=_CollectorHandler(),
            persistence_handler=_CollectorHandler(),
        )
        fanout._dispatch_seq = 0
        fanout._pre_persistence_dispatches = {
            _SlowFalseSeq(i, iteration_started) for i in range(128)
        }

        def _reserve_from_thread() -> None:
            iteration_started.wait(timeout=1)
            for idx in range(16):
                fanout.dispatch_from_thread(
                    noop_loop,
                    ThoughtEvent(source="Agent", content=f"race-{idx}"),
                )
            mutation_done.set()

        reserve_thread = threading.Thread(target=_reserve_from_thread)
        reserve_thread.start()

        await fanout.flush_persistence_barrier()

        reserve_thread.join(timeout=1)
        assert mutation_done.is_set() is True
