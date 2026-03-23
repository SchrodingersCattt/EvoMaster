"""Tests for EventRouter, PersistenceHandler, and SSEHandler.

Covers:
- EventRouter: background consumption, stop/drain, handler exception isolation
- PersistenceHandler: persist vs skip rules (log_line, llm_token, streaming thought)
- SSEHandler: skip rules (assistant_state, planner streaming, direct non-streaming)
  and async/sync send_cb dispatch
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from typing import Any
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from matmaster.core.bus import MessageBus
from matmaster.types.events import (
    AssistantStateEvent,
    BohriumNodeEvent,
    FinishEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from matmaster.integration.event_router import (
    EventHandler,
    EventRouter,
    PersistenceHandler,
    SSEHandler,
)


# ── EventRouter Tests ────────────────────────────────────


class TestEventRouter:
    """EventRouter: background thread consumption and dispatch."""

    def test_router_starts_thread_and_dispatches(self) -> None:
        """Router starts background thread, consumes events, dispatches to handlers."""
        bus = MessageBus()
        received: list[Any] = []

        class Collector:
            def handle(self, event: Any) -> None:
                received.append(event)

        router = EventRouter(bus, [Collector()])
        router.start()

        event = ToolCallEvent(
            source="Agent", call_id="c1", tool_name="bash", arguments={"cmd": "ls"}
        )
        bus.emit(event)
        time.sleep(0.3)  # allow background thread to consume

        router.stop()
        assert len(received) == 1
        assert received[0].call_id == "c1"

    def test_router_stop_drains_remaining_events(self) -> None:
        """stop() drains remaining events from bus before shutdown."""
        bus = MessageBus()
        received: list[Any] = []

        class Collector:
            def handle(self, event: Any) -> None:
                received.append(event)

        router = EventRouter(bus, [Collector()])
        router.start()

        # Emit multiple events quickly
        for i in range(5):
            bus.emit(
                ToolCallEvent(
                    source="Agent",
                    call_id=f"c{i}",
                    tool_name="bash",
                    arguments={},
                )
            )

        time.sleep(0.3)
        router.stop(drain_timeout=2.0)
        assert len(received) == 5

    def test_router_stop_on_empty_bus_completes(self) -> None:
        """stop() on empty bus completes without blocking."""
        bus = MessageBus()
        router = EventRouter(bus, [])
        router.start()

        start = time.monotonic()
        router.stop(drain_timeout=1.0)
        elapsed = time.monotonic() - start
        # Should complete quickly, well under drain_timeout
        assert elapsed < 2.0

    def test_handler_exception_does_not_crash_router(self) -> None:
        """Handler exception is logged; other handlers still receive the event."""
        bus = MessageBus()
        received: list[Any] = []

        class BadHandler:
            def handle(self, event: Any) -> None:
                raise RuntimeError("boom")

        class GoodHandler:
            def handle(self, event: Any) -> None:
                received.append(event)

        router = EventRouter(bus, [BadHandler(), GoodHandler()])
        router.start()

        bus.emit(FinishEvent(source="Agent", reason="done"))
        time.sleep(0.3)
        router.stop()

        assert len(received) == 1
        assert received[0].type == "finish"

    def test_add_handler_post_registration_only_receives_future_events(self) -> None:
        """A handler added after start only receives events emitted after registration."""
        bus = MessageBus()
        initial_received: list[str] = []
        late_received: list[str] = []
        first_event_processed = threading.Event()
        second_event_processed = threading.Event()

        class InitialHandler:
            def handle(self, event: Any) -> None:
                initial_received.append(event.reason)
                if event.reason == "before-registration":
                    first_event_processed.set()
                if event.reason == "after-registration":
                    second_event_processed.set()

        class LateHandler:
            def handle(self, event: Any) -> None:
                late_received.append(event.reason)
                if event.reason == "after-registration":
                    second_event_processed.set()

        router = EventRouter(bus, [InitialHandler()])
        router.start()

        bus.emit(FinishEvent(source="Agent", reason="before-registration"))
        assert first_event_processed.wait(timeout=1.0)

        router.add_handler(LateHandler())

        bus.emit(FinishEvent(source="Agent", reason="after-registration"))
        assert second_event_processed.wait(timeout=1.0)

        router.stop()

        assert initial_received == ["before-registration", "after-registration"]
        assert late_received == ["after-registration"]

    def test_add_handler_during_dispatch_skips_in_flight_event_and_receives_next_event(
        self,
    ) -> None:
        """A handler added during dispatch does not receive the current event."""
        bus = MessageBus()
        late_received: list[str] = []

        class LateHandler:
            def handle(self, event: Any) -> None:
                late_received.append(event.reason)

        late_handler = LateHandler()

        class RegisteringHandler:
            def __init__(self) -> None:
                self._router: EventRouter | None = None

            def bind(self, router: EventRouter) -> None:
                self._router = router

            def handle(self, event: Any) -> None:
                if event.reason == "current":
                    assert self._router is not None
                    self._router.add_handler(late_handler)

        registering_handler = RegisteringHandler()
        router = EventRouter(bus, [registering_handler])
        registering_handler.bind(router)

        router._dispatch(FinishEvent(source="Agent", reason="current"))
        router._dispatch(FinishEvent(source="Agent", reason="next"))

        assert late_received == ["next"]


# ── PersistenceHandler Tests ────────────────────────────


class TestPersistenceHandler:
    """PersistenceHandler: event persistence filter rules."""

    def _make_handler(self) -> tuple[PersistenceHandler, MagicMock]:
        events_table = MagicMock()
        handler = PersistenceHandler(
            events_table=events_table,
            session_id="sess1",
            task_id="task1",
            invocation_id="inv1",
        )
        return handler, events_table

    def test_persists_standard_events(self) -> None:
        """handle() calls events_table.add_event() for standard events."""
        handler, events_table = self._make_handler()

        event = ToolCallEvent(
            source="Agent", call_id="c1", tool_name="bash", arguments={"cmd": "ls"}
        )
        handler.handle(event)

        events_table.add_event.assert_called_once()
        args = events_table.add_event.call_args
        assert args[0][0] == "sess1"  # session_id
        assert args[0][1] == "Agent"  # source
        assert args[0][2] == "tool_call"  # type

    def test_persists_tool_result_and_finish(self) -> None:
        """handle() persists tool_result and finish events."""
        handler, events_table = self._make_handler()

        handler.handle(
            ToolResultEvent(
                source="Agent", call_id="c1", tool_name="bash", result="ok"
            )
        )
        handler.handle(FinishEvent(source="Agent", reason="done"))

        assert events_table.add_event.call_count == 2

    def test_skips_log_line(self) -> None:
        """handle() skips events with type 'log_line'."""
        handler, events_table = self._make_handler()
        # log_line is not in BusEvent union -- simulate by checking _should_persist
        # We test the internal method directly since log_line isn't a BusEvent type
        assert handler._should_persist_type("log_line") is False

    def test_skips_llm_token(self) -> None:
        """handle() skips events with type 'llm_token'."""
        handler, events_table = self._make_handler()
        assert handler._should_persist_type("llm_token") is False

    def test_skips_streaming_thought(self) -> None:
        """handle() skips ThoughtEvent with stream_state in (start, streaming, end)."""
        handler, events_table = self._make_handler()

        for state in ("start", "streaming", "end"):
            handler.handle(
                ThoughtEvent(source="Agent", content="thinking", stream_state=state)
            )

        events_table.add_event.assert_not_called()

    def test_persists_non_streaming_thought(self) -> None:
        """handle() persists ThoughtEvent with stream_state=None."""
        handler, events_table = self._make_handler()

        handler.handle(
            ThoughtEvent(source="Agent", content="complete thought", stream_state=None)
        )

        events_table.add_event.assert_called_once()

    def test_persists_bohrium_node_payload_as_content(self) -> None:
        """handle() persists Bohrium node payload as the content field."""
        handler, events_table = self._make_handler()
        bohrium_payload = {
            "type": "setup_ready",
            "content": {"node_id": "node-1"},
            "phase": "ssh",
        }

        handler.handle(
            BohriumNodeEvent(source="BohriumSetup", payload=bohrium_payload)
        )

        events_table.add_event.assert_called_once()
        args = events_table.add_event.call_args
        assert args[0][0] == "sess1"
        assert args[0][1] == "BohriumSetup"
        assert args[0][2] == "bohrium_node"
        assert args[0][3] == bohrium_payload


# ── SSEHandler Tests ────────────────────────────────────


class TestSSEHandler:
    """SSEHandler: event push filter rules and async/sync dispatch."""

    def test_sends_standard_events(self) -> None:
        """handle() calls send_cb for standard events."""
        send_cb = MagicMock()
        handler = SSEHandler(
            send_cb=send_cb,
            loop=None,
            session_id="sess1",
            task_id="task1",
            invocation_id="inv1",
            mode="direct",
        )

        handler.handle(FinishEvent(source="Agent", reason="done"))

        send_cb.assert_called_once()
        payload = send_cb.call_args[0][0]
        assert payload["type"] == "finish"
        assert payload["session_id"] == "sess1"

    def test_skips_assistant_state(self) -> None:
        """handle() skips AssistantStateEvent."""
        send_cb = MagicMock()
        handler = SSEHandler(
            send_cb=send_cb,
            loop=None,
            session_id="sess1",
            task_id="task1",
            invocation_id=None,
            mode="direct",
        )

        handler.handle(AssistantStateEvent(source="Agent", state={"role": "assistant"}))

        send_cb.assert_not_called()

    def test_skips_planner_streaming_thought(self) -> None:
        """handle() skips streaming ThoughtEvent when source is Planner."""
        send_cb = MagicMock()
        handler = SSEHandler(
            send_cb=send_cb,
            loop=None,
            session_id="sess1",
            task_id="task1",
            invocation_id=None,
            mode="planner",
        )

        for state in ("start", "streaming", "end"):
            handler.handle(
                ThoughtEvent(
                    source="Planner", content="planning", stream_state=state
                )
            )

        send_cb.assert_not_called()

    def test_skips_direct_non_streaming_thought(self) -> None:
        """handle() skips non-streaming ThoughtEvent in direct mode."""
        send_cb = MagicMock()
        handler = SSEHandler(
            send_cb=send_cb,
            loop=None,
            session_id="sess1",
            task_id="task1",
            invocation_id=None,
            mode="direct",
        )

        handler.handle(
            ThoughtEvent(source="Agent", content="full thought", stream_state=None)
        )

        send_cb.assert_not_called()

    def test_async_send_with_loop(self) -> None:
        """handle() uses asyncio.run_coroutine_threadsafe when loop is present."""
        loop = asyncio.new_event_loop()
        received: list[dict] = []

        async def async_send(payload: dict) -> None:
            received.append(payload)

        handler = SSEHandler(
            send_cb=async_send,
            loop=loop,
            session_id="sess1",
            task_id="task1",
            invocation_id=None,
            mode="direct",
        )

        # Run loop in background thread
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        try:
            handler.handle(
                ToolResultEvent(
                    source="Agent", call_id="c1", tool_name="bash", result="ok"
                )
            )
            time.sleep(0.3)  # allow async dispatch
            assert len(received) == 1
            assert received[0]["type"] == "tool_result"
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)
            loop.close()

    def test_sync_send_without_loop(self) -> None:
        """handle() calls send_cb directly when loop is None."""
        send_cb = MagicMock()
        handler = SSEHandler(
            send_cb=send_cb,
            loop=None,
            session_id="sess1",
            task_id="task1",
            invocation_id=None,
            mode="planner",
        )

        handler.handle(
            ToolCallEvent(
                source="Agent", call_id="c1", tool_name="bash", arguments={}
            )
        )

        send_cb.assert_called_once()
