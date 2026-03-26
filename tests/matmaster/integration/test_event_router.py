"""Tests for EventRouter, PersistenceHandler, and SSEHandler.

Covers:
- EventRouter: background consumption, stop/drain, handler exception isolation
- PersistenceHandler: persist vs skip rules (log_line, llm_token, streaming thought)
- SSEHandler: skip rules (assistant_state, planner streaming, direct non-streaming)
  and async/sync send_cb dispatch
"""

from __future__ import annotations

import asyncio
import json
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
    CancelledEvent,
    ConfirmationRequestEvent,
    ConfirmationTimeoutEvent,
    ErrorEvent,
    ExpRunEvent,
    McpConnectEvent,
    McpServerStatusEvent,
    ResponseEvent,
    RunResultEvent,
    SkillHitEvent,
    StreamClosedEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from matmaster.integration.event_router import EventHandler, EventRouter
from matmaster.integration.persistence_handler import PersistenceHandler
from matmaster.integration.sse_handler import SSEHandler


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

        bus.emit(RunResultEvent(source="Agent", reason="done"))
        time.sleep(0.3)
        router.stop()

        assert len(received) == 1
        assert received[0].type == "run_result"

    def test_router_stop_waits_for_handler_close(self) -> None:
        """stop() should call handler.close() and wait for it."""
        bus = MessageBus()
        closed = threading.Event()

        class SlowCloser:
            def handle(self, event: Any) -> None:
                return None

            def close(self) -> None:
                time.sleep(0.2)
                closed.set()

        router = EventRouter(bus, [SlowCloser()])
        router.start()

        start = time.monotonic()
        router.stop()
        elapsed = time.monotonic() - start

        assert closed.is_set()
        assert elapsed >= 0.2

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

        bus.emit(RunResultEvent(source="Agent", reason="before-registration"))
        assert first_event_processed.wait(timeout=1.0)

        router.add_handler(LateHandler())

        bus.emit(RunResultEvent(source="Agent", reason="after-registration"))
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

        router._dispatch(RunResultEvent(source="Agent", reason="current"))
        router._dispatch(RunResultEvent(source="Agent", reason="next"))

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
        kwargs = args.kwargs or {}
        assert kwargs.get("task_id") == "task1"
        assert kwargs.get("invocation_id") == "inv1"
        assert kwargs.get("spawn_id") is None

    def test_persists_tool_result_and_run_result(self) -> None:
        """handle() persists tool_result and run_result events."""
        handler, events_table = self._make_handler()

        handler.handle(
            ToolResultEvent(
                source="Agent", call_id="c1", tool_name="bash", result="ok"
            )
        )
        handler.handle(RunResultEvent(source="Agent", reason="done"))

        assert events_table.add_event.call_count == 2

    def test_skips_stream_closed_event(self) -> None:
        """handle() skips stream_closed events because they are SSE-only lifecycle markers."""
        handler, events_table = self._make_handler()

        handler.handle(StreamClosedEvent(source="System"))

        events_table.add_event.assert_not_called()

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

    def test_persists_non_streaming_response(self) -> None:
        handler, events_table = self._make_handler()

        handler.handle(ResponseEvent(source="Agent", content="done"))

        events_table.add_event.assert_called_once()

    def test_skips_streaming_response_in_persistence(self) -> None:
        handler, events_table = self._make_handler()

        handler.handle(
            ResponseEvent(source="Agent", content="tok", stream_state="streaming")
        )

        events_table.add_event.assert_not_called()

    def test_tool_call_persists_public_shape(self) -> None:
        handler, events_table = self._make_handler()

        handler.handle(
            ToolCallEvent(
                source="Agent",
                call_id="c1",
                tool_name="bash",
                arguments={"cmd": "ls"},
            )
        )

        args = events_table.add_event.call_args[0]
        assert args[3] == {
            "id": "c1",
            "call_id": "c1",
            "name": "bash",
            "args": {"cmd": "ls"},
        }

    def test_tool_result_persists_public_shape(self) -> None:
        handler, events_table = self._make_handler()

        handler.handle(
            ToolResultEvent(
                source="Agent",
                call_id="c1",
                tool_name="bash",
                result="file.txt",
                info={"auto_save": True},
            )
        )

        args = events_table.add_event.call_args[0]
        assert args[3] == {
            "id": "c1",
            "call_id": "c1",
            "name": "bash",
            "result": "file.txt",
            "status": "success",
            "info": {"auto_save": True},
        }

    def test_run_result_persists_content_status_reason(self) -> None:
        handler, events_table = self._make_handler()

        handler.handle(
            RunResultEvent(
                source="Agent",
                status="completed",
                reason="natural",
                final_content="here are your files",
            )
        )

        args = events_table.add_event.call_args[0]
        assert args[3] == {
            "content": "here are your files",
            "status": "completed",
            "reason": "natural",
        }

    def test_assistant_state_persists_state_dict(self) -> None:
        handler, events_table = self._make_handler()

        state = {"role": "assistant", "content": "hi", "tool_calls": []}
        handler.handle(AssistantStateEvent(source="Agent", state=state))

        args = events_table.add_event.call_args[0]
        assert args[3] == state

    def test_bohrium_node_persists_flattened_public_shape(self) -> None:
        handler, events_table = self._make_handler()

        handler.handle(
            BohriumNodeEvent(
                source="BohriumSetup",
                payload={
                    "type": "setup_ready",
                    "content": {
                        "status": "ready",
                        "message": "Node ready",
                        "node_id": 1,
                    },
                    "phase": "ssh",
                },
            )
        )

        args = events_table.add_event.call_args[0]
        assert args[3] == {
            "status": "ready",
            "message": "Node ready",
            "node_id": 1,
            "event_type": "setup_ready",
            "phase": "ssh",
        }

    def test_persists_spawn_id_kwarg_for_subagent_events(self) -> None:
        """handle() passes event.spawn_id into add_event(spawn_id=...) for replay grouping."""
        handler, events_table = self._make_handler()

        handler.handle(
            ToolCallEvent(
                source="MatMaster:explore",
                call_id="c1",
                tool_name="read",
                arguments={"path": "x"},
                spawn_id="a1b2c3d4e5f67890",
            )
        )

        events_table.add_event.assert_called_once()
        kwargs = events_table.add_event.call_args.kwargs
        assert kwargs.get("spawn_id") == "a1b2c3d4e5f67890"


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

        handler.handle(RunResultEvent(source="Agent", reason="done"))

        send_cb.assert_called_once()
        payload = send_cb.call_args[0][0]
        assert payload["type"] == "run_result"
        assert payload["source"] == "MatMaster"
        assert payload["session_id"] == "sess1"
        assert payload.get("spawn_id") is None

    def test_sse_payload_includes_spawn_id_at_top_level(self) -> None:
        """Live SSE payloads expose spawn_id next to session_id/task_id for subagent events."""
        send_cb = MagicMock()
        handler = SSEHandler(
            send_cb=send_cb,
            loop=None,
            session_id="sess1",
            task_id="task1",
            invocation_id="inv1",
            mode="planner",
        )

        handler.handle(
            ToolCallEvent(
                source="MatMaster:explore",
                call_id="c1",
                tool_name="bash",
                arguments={"cmd": "ls"},
                spawn_id="feedfacecafe0001",
            )
        )

        payload = send_cb.call_args[0][0]
        assert payload.get("spawn_id") == "feedfacecafe0001"
        assert isinstance(payload.get("content"), dict)

    def test_sends_json_safe_payload(self) -> None:
        """handle() emits payloads that are safe for SSE/Redis JSON encoding."""
        send_cb = MagicMock()
        handler = SSEHandler(
            send_cb=send_cb,
            loop=None,
            session_id="sess1",
            task_id="task1",
            invocation_id="inv1",
            mode="direct",
        )

        handler.handle(RunResultEvent(source="Agent", reason="done"))

        payload = send_cb.call_args[0][0]
        json.dumps(payload, ensure_ascii=False)
        assert isinstance(payload["timestamp"], str)

    def test_tool_call_payload_matches_frontend_contract(self) -> None:
        """tool_call payload exposes nested content expected by the frontend."""
        send_cb = MagicMock()
        handler = SSEHandler(
            send_cb=send_cb,
            loop=None,
            session_id="sess1",
            task_id="task1",
            invocation_id="inv1",
            mode="direct",
        )

        handler.handle(
            ToolCallEvent(
                source="Agent",
                call_id="call-1",
                tool_name="bash",
                arguments={"cmd": "ls"},
            )
        )

        payload = send_cb.call_args[0][0]
        assert payload["source"] == "MatMaster"
        assert payload["content"] == {
            "id": "call-1",
            "call_id": "call-1",
            "name": "bash",
            "args": {"cmd": "ls"},
        }

    def test_tool_result_payload_matches_frontend_contract(self) -> None:
        """tool_result payload exposes nested content expected by the frontend."""
        send_cb = MagicMock()
        handler = SSEHandler(
            send_cb=send_cb,
            loop=None,
            session_id="sess1",
            task_id="task1",
            invocation_id="inv1",
            mode="direct",
        )

        handler.handle(
            ToolResultEvent(
                source="Agent",
                call_id="call-1",
                tool_name="bash",
                result={"status": "success", "stdout": "ok"},
                info={"auto_save": True},
            )
        )

        payload = send_cb.call_args[0][0]
        assert payload["content"] == {
            "id": "call-1",
            "call_id": "call-1",
            "name": "bash",
            "result": {"status": "success", "stdout": "ok"},
            "status": "success",
            "info": {"auto_save": True},
        }

    def test_confirmation_request_payload_matches_frontend_contract(self) -> None:
        """confirmation_request payload exposes question and actions via content."""
        send_cb = MagicMock()
        handler = SSEHandler(
            send_cb=send_cb,
            loop=None,
            session_id="sess1",
            task_id="task1",
            invocation_id="inv1",
            mode="direct",
        )

        handler.handle(
            ConfirmationRequestEvent(
                source="MatMaster",
                question="Proceed?",
                mode="timeout",
                timeout_seconds=20,
                actions=["yes", "no"],
                context="ctx",
                origin="planner",
            )
        )

        payload = send_cb.call_args[0][0]
        assert payload["content"] == {
            "question": "Proceed?",
            "mode": "timeout",
            "timeout_seconds": 20,
            "context": "ctx",
            "actions": ["yes", "no"],
            "origin": "planner",
        }

    def test_error_payload_exposes_message_via_content(self) -> None:
        """error payload exposes message text under content for frontend rendering."""
        send_cb = MagicMock()
        handler = SSEHandler(
            send_cb=send_cb,
            loop=None,
            session_id="sess1",
            task_id="task1",
            invocation_id="inv1",
            mode="direct",
        )

        handler.handle(ErrorEvent(source="System", message="boom", traceback="tb"))

        payload = send_cb.call_args[0][0]
        assert payload["content"] == {"message": "boom", "traceback": "tb"}

    def test_bohrium_node_payload_flattens_wrapped_content(self) -> None:
        """bohrium_node payload unwraps nested node status into content."""
        send_cb = MagicMock()
        handler = SSEHandler(
            send_cb=send_cb,
            loop=None,
            session_id="sess1",
            task_id="task1",
            invocation_id="inv1",
            mode="direct",
        )

        handler.handle(
            BohriumNodeEvent(
                source="BohriumSetup",
                payload={
                    "type": "setup_ready",
                    "content": {
                        "status": "ready",
                        "message": "Node ready",
                        "node_id": 1,
                    },
                    "phase": "ssh",
                },
            )
        )

        payload = send_cb.call_args[0][0]
        assert payload["source"] == "MatMaster"
        assert payload["content"] == {
            "status": "ready",
            "message": "Node ready",
            "node_id": 1,
            "event_type": "setup_ready",
            "phase": "ssh",
        }

    def test_mcp_server_status_payload_uses_content_object(self) -> None:
        """mcp_server_status payload exposes merged detail in content."""
        send_cb = MagicMock()
        handler = SSEHandler(
            send_cb=send_cb,
            loop=None,
            session_id="sess1",
            task_id="task1",
            invocation_id="inv1",
            mode="direct",
        )

        handler.handle(
            McpServerStatusEvent(
                source="System",
                server_name="code-server",
                transport="sse",
                phase="retrying",
                detail={
                    "message": "retrying",
                    "attempt": 2,
                    "max_attempts": 3,
                    "error": "timeout",
                },
            )
        )

        payload = send_cb.call_args[0][0]
        assert payload["content"] == {
            "server_name": "code-server",
            "transport": "sse",
            "phase": "retrying",
            "message": "retrying",
            "attempt": 2,
            "max_attempts": 3,
            "error": "timeout",
        }

    def test_mcp_connect_payload_uses_content_object(self) -> None:
        """mcp_connect payload exposes phase and message via content."""
        send_cb = MagicMock()
        handler = SSEHandler(
            send_cb=send_cb,
            loop=None,
            session_id="sess1",
            task_id="task1",
            invocation_id="inv1",
            mode="direct",
        )

        handler.handle(
            McpConnectEvent(
                source="System",
                phase="ready",
                message="connected",
                elapsed_ms=123,
            )
        )

        payload = send_cb.call_args[0][0]
        assert payload["content"] == {
            "phase": "ready",
            "message": "connected",
            "elapsed_ms": 123,
            "error": None,
        }

    def test_sends_stream_closed_event(self) -> None:
        """handle() forwards stream_closed events for frontend stream completion."""
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
            StreamClosedEvent(
                source="System",
                task_completed=True,
                end_reason="natural",
            )
        )

        send_cb.assert_called_once()
        payload = send_cb.call_args[0][0]
        assert payload["type"] == "stream_closed"
        assert payload["source"] == "System"
        assert payload["task_completed"] is True

    def test_sse_handler_sends_response_payload(self) -> None:
        send_cb = MagicMock()
        handler = SSEHandler(
            send_cb=send_cb,
            loop=None,
            session_id="sess1",
            task_id="task1",
            invocation_id=None,
            mode="direct",
        )

        handler.handle(ResponseEvent(source="Agent", content="hello"))

        send_cb.assert_called_once()
        payload = send_cb.call_args[0][0]
        assert payload["type"] == "response"
        assert payload["source"] == "MatMaster"
        assert payload["content"] == "hello"

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

    def test_skips_complete_segment_events(self) -> None:
        """handle() skips persisted complete-segment snapshots on the live SSE path."""
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
            ThoughtEvent(source="Agent", content="full thought", stream_state="complete")
        )
        handler.handle(
            ResponseEvent(source="Agent", content="full answer", stream_state="complete")
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


# ── _public_content_for_event Tests ────────────────────


class TestPublicContentForEvent:
    """_public_content_for_event covers every persisted public event family."""

    def test_run_result_extracts_final_content(self) -> None:
        from matmaster.integration.event_payloads import _public_content_for_event

        payload = {
            "type": "run_result",
            "source": "Agent",
            "status": "completed",
            "reason": "natural",
            "final_content": "here are your files",
        }

        assert _public_content_for_event("run_result", payload) == {
            "content": "here are your files",
            "status": "completed",
            "reason": "natural",
        }

    def test_finish_alias_uses_same_shape(self) -> None:
        from matmaster.integration.event_payloads import _public_content_for_event

        payload = {
            "type": "finish",
            "source": "Agent",
            "status": "completed",
            "reason": "",
            "final_content": "legacy done",
        }

        assert _public_content_for_event("finish", payload) == {
            "content": "legacy done",
            "status": "completed",
            "reason": "",
        }

    def test_assistant_state_returns_state_dict(self) -> None:
        from matmaster.integration.event_payloads import _public_content_for_event

        state = {"role": "assistant", "content": "hi", "tool_calls": []}
        payload = {"type": "assistant_state", "source": "Agent", "state": state}

        assert _public_content_for_event("assistant_state", payload) == state

    def test_skill_hit_returns_skill_name(self) -> None:
        from matmaster.integration.event_payloads import _public_content_for_event

        payload = {"type": "skill_hit", "source": "Agent", "skill_name": "search"}

        assert _public_content_for_event("skill_hit", payload) == {
            "skill_name": "search"
        }

    def test_cancelled_returns_reason(self) -> None:
        from matmaster.integration.event_payloads import _public_content_for_event

        payload = {"type": "cancelled", "source": "System", "reason": "user stop"}

        assert _public_content_for_event("cancelled", payload) == {
            "reason": "user stop"
        }

    def test_confirmation_timeout_returns_question_and_default(self) -> None:
        from matmaster.integration.event_payloads import _public_content_for_event

        payload = {
            "type": "confirmation_timeout",
            "source": "System",
            "question": "Proceed?",
            "default_reply": "yes",
        }

        assert _public_content_for_event("confirmation_timeout", payload) == {
            "question": "Proceed?",
            "default_reply": "yes",
        }

    def test_exp_run_returns_exp_name(self) -> None:
        from matmaster.integration.event_payloads import _public_content_for_event

        payload = {"type": "exp_run", "source": "System", "exp_name": "vasp-relax"}

        assert _public_content_for_event("exp_run", payload) == {
            "exp_name": "vasp-relax"
        }

    def test_response_uses_content_field(self) -> None:
        from matmaster.integration.event_payloads import _public_content_for_event

        payload = {"type": "response", "source": "Agent", "content": "hello"}

        assert _public_content_for_event("response", payload) == "hello"

    def test_unknown_type_without_content_extracts_business_fields(self) -> None:
        from matmaster.integration.event_payloads import _public_content_for_event

        payload = {
            "type": "new_future_event",
            "source": "System",
            "timestamp": "2026-03-24T00:00:00",
            "custom_data": {"key": "value"},
            "detail": "info",
        }

        assert _public_content_for_event("new_future_event", payload) == {
            "custom_data": {"key": "value"},
            "detail": "info",
        }

    def test_unknown_type_with_content_keeps_existing_behavior(self) -> None:
        from matmaster.integration.event_payloads import _public_content_for_event

        payload = {"type": "future_event", "source": "System", "content": "data"}

        assert _public_content_for_event("future_event", payload) == "data"
