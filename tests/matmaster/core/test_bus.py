"""Tests for MessageBus async event queue."""

import asyncio
import threading

import pytest

from matmaster.core.bus import MessageBus
from matmaster.types.events import ThoughtEvent


def _make_thought(content: str = "hello", source: str = "agent") -> ThoughtEvent:
    return ThoughtEvent(source=source, content=content)


class TestMessageBusBasic:
    """Basic emit/get operations."""

    async def test_emit_and_get(self) -> None:
        bus = MessageBus()
        event = _make_thought("hello")
        await bus.emit(event)
        got = await bus.get()
        assert got.content == "hello"
        assert got.source == "agent"

    async def test_fifo_order(self) -> None:
        bus = MessageBus()
        await bus.emit(_make_thought("A"))
        await bus.emit(_make_thought("B"))
        await bus.emit(_make_thought("C"))
        assert (await bus.get()).content == "A"
        assert (await bus.get()).content == "B"
        assert (await bus.get()).content == "C"

    async def test_pending_count(self) -> None:
        bus = MessageBus()
        await bus.emit(_make_thought("1"))
        await bus.emit(_make_thought("2"))
        await bus.emit(_make_thought("3"))
        assert bus.pending == 3
        await bus.get()
        assert bus.pending == 2

    async def test_empty_property(self) -> None:
        bus = MessageBus()
        assert bus.empty is True
        await bus.emit(_make_thought("x"))
        assert bus.empty is False
        await bus.get()
        assert bus.empty is True


class TestMessageBusTimeout:
    """Timeout and non-blocking operations."""

    async def test_get_timeout_on_empty(self) -> None:
        bus = MessageBus()
        with pytest.raises(asyncio.TimeoutError):
            await bus.get(timeout=0.05)

    async def test_get_nowait_on_empty(self) -> None:
        bus = MessageBus()
        with pytest.raises(asyncio.QueueEmpty):
            bus.get_nowait()


class TestMessageBusConcurrency:
    """Async concurrency tests (replaces threading tests)."""

    async def test_concurrent_emit_and_get(self) -> None:
        """Multiple coroutines can emit concurrently and all events are received."""
        bus = MessageBus()
        num_producers = 10
        events_per_producer = 100

        async def emitter(producer_id: int) -> None:
            for i in range(events_per_producer):
                await bus.emit(
                    _make_thought(f"p{producer_id}-{i}", source=f"producer-{producer_id}")
                )

        await asyncio.gather(*(emitter(p) for p in range(num_producers)))

        collected = []
        while not bus.empty:
            collected.append(bus.get_nowait())
        assert len(collected) == num_producers * events_per_producer


class TestMessageBusEmitNowait:
    """Thread-safe sync emit via emit_nowait."""

    async def test_emit_nowait_sync_fallback(self) -> None:
        """emit_nowait without loop set falls back to direct put_nowait."""
        bus = MessageBus()
        event = _make_thought("sync-emit")
        bus.emit_nowait(event)
        got = await bus.get()
        assert got.content == "sync-emit"

    async def test_emit_nowait_cross_thread(self) -> None:
        """emit_nowait from a different thread uses call_soon_threadsafe."""
        bus = MessageBus()
        loop = asyncio.get_running_loop()
        bus.set_loop(loop)

        event = _make_thought("cross-thread")
        received = asyncio.Event()

        def emit_from_thread() -> None:
            bus.emit_nowait(event)

        t = threading.Thread(target=emit_from_thread)
        t.start()
        t.join(timeout=2.0)

        got = await bus.get(timeout=1.0)
        assert got.content == "cross-thread"

    async def test_set_loop(self) -> None:
        """set_loop captures event loop reference."""
        bus = MessageBus()
        assert bus._loop is None
        loop = asyncio.get_running_loop()
        bus.set_loop(loop)
        assert bus._loop is loop


class TestMessageBusMaxsize:
    """Maxsize configuration."""

    async def test_maxsize(self) -> None:
        bus = MessageBus(maxsize=1)
        await bus.emit(_make_thought("only"))
        assert bus.pending == 1
