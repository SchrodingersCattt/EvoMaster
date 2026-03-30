"""Tests for MessageBus async event queue."""

import asyncio

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
    """Async concurrency tests."""

    async def test_concurrent_emit_get(self) -> None:
        bus = MessageBus()
        count = 100

        async def emitter() -> None:
            for i in range(count):
                await bus.emit(_make_thought(f"e-{i}"))

        async def consumer() -> list[str]:
            results = []
            for _ in range(count):
                ev = await bus.get(timeout=2.0)
                results.append(ev.content)
            return results

        _, results = await asyncio.gather(emitter(), consumer())
        assert len(results) == count


class TestMessageBusEmitNowait:
    """emit_nowait tests."""

    async def test_emit_nowait_sync(self) -> None:
        """emit_nowait puts event directly into queue."""
        bus = MessageBus()
        bus.emit_nowait(_make_thought("sync"))
        got = await bus.get()
        assert got.content == "sync"


class TestMessageBusMaxsize:
    """Maxsize configuration."""

    async def test_maxsize(self) -> None:
        bus = MessageBus(maxsize=1)
        await bus.emit(_make_thought("only"))
        assert bus.pending == 1
