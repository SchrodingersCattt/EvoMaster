"""Tests for MessageBus synchronous event queue."""

import queue
import threading

from matmaster.core.bus import MessageBus
from matmaster.types.events import ThoughtEvent


def _make_thought(content: str = "hello", source: str = "agent") -> ThoughtEvent:
    return ThoughtEvent(source=source, content=content)


class TestMessageBusBasic:
    """Basic emit/get operations."""

    def test_emit_and_get(self) -> None:
        bus = MessageBus()
        event = _make_thought("hello")
        bus.emit(event)
        got = bus.get()
        assert got.content == "hello"
        assert got.source == "agent"

    def test_fifo_order(self) -> None:
        bus = MessageBus()
        bus.emit(_make_thought("A"))
        bus.emit(_make_thought("B"))
        bus.emit(_make_thought("C"))
        assert bus.get().content == "A"
        assert bus.get().content == "B"
        assert bus.get().content == "C"

    def test_pending_count(self) -> None:
        bus = MessageBus()
        bus.emit(_make_thought("1"))
        bus.emit(_make_thought("2"))
        bus.emit(_make_thought("3"))
        assert bus.pending == 3
        bus.get()
        assert bus.pending == 2

    def test_empty_property(self) -> None:
        bus = MessageBus()
        assert bus.empty is True
        bus.emit(_make_thought("x"))
        assert bus.empty is False
        bus.get()
        assert bus.empty is True


class TestMessageBusTimeout:
    """Timeout and non-blocking operations."""

    def test_get_timeout_on_empty(self) -> None:
        bus = MessageBus()
        try:
            bus.get(timeout=0.05)
            assert False, "Expected queue.Empty"
        except queue.Empty:
            pass

    def test_get_nowait_on_empty(self) -> None:
        bus = MessageBus()
        try:
            bus.get_nowait()
            assert False, "Expected queue.Empty"
        except queue.Empty:
            pass


class TestMessageBusThreading:
    """Thread safety tests."""

    def test_thread_safety(self) -> None:
        bus = MessageBus()
        num_threads = 10
        events_per_thread = 100

        def emitter(thread_id: int) -> None:
            for i in range(events_per_thread):
                bus.emit(_make_thought(f"t{thread_id}-{i}", source=f"thread-{thread_id}"))

        threads = [threading.Thread(target=emitter, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        collected = []
        while not bus.empty:
            collected.append(bus.get_nowait())
        assert len(collected) == num_threads * events_per_thread


class TestMessageBusMaxsize:
    """Maxsize configuration."""

    def test_maxsize(self) -> None:
        bus = MessageBus(maxsize=1)
        bus.emit(_make_thought("only"))
        assert bus.pending == 1
