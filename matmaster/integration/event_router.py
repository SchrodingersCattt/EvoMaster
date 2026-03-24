"""EventRouter -- single-consumer multi-handler event dispatch.

Replaces the 130-line event_callback closure in agent_run_service.py,
decomposing 5 mixed responsibilities into independently testable handlers.

Components:
- EventHandler: Protocol for handler interface
- EventRouter: background thread consumer + multi-handler dispatch
- PersistenceHandler: persists events to DB (migrated filter rules)
- SSEHandler: pushes events to SSE send_cb (migrated filter rules)

Lifecycle: EventRouter is bound to a single run (per D-15).
Created in run_agent_sync(), stopped in finally block.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from typing import Any, Callable, Protocol, runtime_checkable

from matmaster.core.bus import MessageBus
from matmaster.types.events import BusEvent, ResponseEvent, ThoughtEvent


def _normalize_public_source(source: object) -> str:
    """Collapse internal source labels to the public SSE set."""
    raw = str(source or "").strip()
    if raw in {"User", "System"}:
        return raw
    return "MatMaster"


def _flatten_bohrium_content(raw_payload: object) -> object:
    """Unwrap Bohrium callback payloads into the frontend-facing content shape."""
    if not isinstance(raw_payload, dict):
        return raw_payload

    nested = raw_payload.get("content")
    extras = {
        key: value
        for key, value in raw_payload.items()
        if key not in {"content", "type"}
    }

    if isinstance(nested, dict):
        content: dict[str, Any] = {**nested, **extras}
    elif nested is None:
        content = extras
    else:
        content = {"message": nested, **extras}

    event_type = raw_payload.get("type")
    if event_type is not None and "event_type" not in content:
        content["event_type"] = event_type

    return content


def _public_content_for_event(
    event_type: str, payload: dict[str, Any]
) -> object | None:
    """Adapt internal event payloads to the frontend SSE contract."""
    if event_type == "tool_call":
        call_id = payload.get("call_id")
        return {
            "id": call_id,
            "call_id": call_id,
            "name": payload.get("tool_name"),
            "args": payload.get("arguments") or {},
        }

    if event_type == "tool_result":
        call_id = payload.get("call_id")
        return {
            "id": call_id,
            "call_id": call_id,
            "name": payload.get("tool_name"),
            "result": payload.get("result"),
            "info": payload.get("info") or {},
        }

    if event_type == "confirmation_request":
        return {
            "question": payload.get("question"),
            "mode": payload.get("mode"),
            "timeout_seconds": payload.get("timeout_seconds"),
            "context": payload.get("context"),
            "actions": payload.get("actions") or [],
            "origin": payload.get("origin"),
        }

    if event_type == "error":
        return {
            "message": payload.get("message"),
            "traceback": payload.get("traceback"),
        }

    if event_type == "workspace_upload_error":
        return {"message": payload.get("message")}

    if event_type == "bohrium_node":
        return _flatten_bohrium_content(payload.get("payload"))

    if event_type == "mcp_server_status":
        detail = payload.get("detail")
        content = {
            "server_name": payload.get("server_name"),
            "transport": payload.get("transport"),
            "phase": payload.get("phase"),
        }
        if isinstance(detail, dict):
            content.update(detail)
        return content

    if event_type == "mcp_connect":
        return {
            "phase": payload.get("phase"),
            "message": payload.get("message"),
            "elapsed_ms": payload.get("elapsed_ms"),
            "error": payload.get("error"),
        }

    if event_type == "context_compaction":
        return payload.get("payload")

    if event_type in ("run_result", "finish"):
        return {
            "content": payload.get("final_content") or "",
            "status": payload.get("status"),
            "reason": payload.get("reason"),
        }

    if event_type == "assistant_state":
        return payload.get("state")

    if event_type == "skill_hit":
        return {"skill_name": payload.get("skill_name")}

    if event_type == "cancelled":
        return {"reason": payload.get("reason", "")}

    if event_type == "confirmation_timeout":
        return {
            "question": payload.get("question"),
            "default_reply": payload.get("default_reply"),
        }

    if event_type == "exp_run":
        return {"exp_name": payload.get("exp_name")}

    return payload.get("content")

logger = logging.getLogger(__name__)


# ── EventHandler Protocol ────────────────────────────────


@runtime_checkable
class EventHandler(Protocol):
    """Protocol for event handlers consumed by EventRouter."""

    def handle(self, event: BusEvent) -> None:  # type: ignore[arg-type]
        """Process a single bus event."""
        ...


# ── EventRouter ──────────────────────────────────────────


class EventRouter:
    """Background thread consumer that dispatches events to handlers.

    Single-consumer pattern: consumes from MessageBus in a daemon thread,
    dispatches each event to all registered handlers.

    Lifecycle bound to a single run (D-15):
    - start(): spawns daemon thread
    - stop(drain_timeout): joins consumer, drains queue, closes handlers
    """

    def __init__(self, bus: MessageBus, handlers: list[EventHandler]) -> None:
        self._bus = bus
        self._handlers = handlers
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Spawn daemon thread running the consume loop."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._consume_loop, daemon=True, name="event-router"
        )
        self._thread.start()

    def add_handler(self, handler: EventHandler) -> None:
        """Register a new handler for future dispatches."""
        self._handlers = [*self._handlers, handler]

    def stop(self, drain_timeout: float = 2.0) -> None:
        """Signal stop, wait for consumer, drain remaining events, close handlers.

        Args:
            drain_timeout: max seconds to spend draining queued events
                after the consumer thread exits.
        """
        self._stop_event.set()

        if self._thread is not None:
            self._thread.join()
            self._thread = None

        # Drain remaining events from bus within deadline
        deadline = time.monotonic() + drain_timeout
        while time.monotonic() < deadline:
            try:
                event = self._bus.get_nowait()
                self._dispatch(event)
            except queue.Empty:
                break

        self._close_handlers()

    def _consume_loop(self) -> None:
        """Main consume loop -- runs in background thread."""
        while not self._stop_event.is_set():
            try:
                event = self._bus.get(timeout=0.1)
                self._dispatch(event)
            except queue.Empty:
                continue

    def _dispatch(self, event: BusEvent) -> None:  # type: ignore[arg-type]
        """Dispatch event to all handlers, catching exceptions."""
        handlers = self._handlers
        for handler in handlers:
            try:
                handler.handle(event)
            except Exception:
                logger.warning(
                    "Handler %s raised exception for event type=%s",
                    type(handler).__name__,
                    getattr(event, "type", "?"),
                    exc_info=True,
                )

    def _close_handlers(self) -> None:
        """Flush handler-owned resources after dispatch stops."""
        for handler in self._handlers:
            close = getattr(handler, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except Exception:
                logger.warning(
                    "Handler %s raised during close()",
                    type(handler).__name__,
                    exc_info=True,
                )


# ── PersistenceHandler ───────────────────────────────────


class PersistenceHandler:
    """Persists events to database via events_table.add_event().

    Filter rules migrated from _should_persist_event in agent_run_service.py:
    - Skip: log_line, llm_token
    - Skip: streaming ThoughtEvent / ResponseEvent deltas
    - Persist: everything else
    """

    _SKIP_TYPES = frozenset({"log_line", "llm_token", "stream_closed", "end"})
    _STREAMING_STATES = frozenset({"start", "streaming", "end"})

    def __init__(
        self,
        events_table: Any,
        session_id: str,
        task_id: str,
        invocation_id: str | None = None,
    ) -> None:
        self._events_table = events_table
        self._session_id = session_id
        self._task_id = task_id
        self._invocation_id = invocation_id

    def handle(self, event: BusEvent) -> None:  # type: ignore[arg-type]
        """Persist event to DB if it passes filter rules."""
        event_type = getattr(event, "type", "")

        if not self._should_persist_type(event_type):
            return

        # Skip streaming thought/response events (ephemeral deltas)
        if isinstance(event, (ThoughtEvent, ResponseEvent)) and event.stream_state in self._STREAMING_STATES:
            return

        # Use the same JSON-safe payload mode as SSEHandler so persistence
        # and live SSE derive content from the same normalized field values.
        payload = event.model_dump(mode="json")
        content = _public_content_for_event(event_type, payload)

        try:
            self._events_table.add_event(
                self._session_id,
                event.source,
                event_type,
                content,
                self._task_id,
                invocation_id=self._invocation_id,
            )
        except Exception:
            logger.error(
                "Failed to persist event type=%s session_id=%s",
                event_type,
                self._session_id,
                exc_info=True,
            )

    def _should_persist_type(self, event_type: str) -> bool:
        """Check if event type should be persisted (type-level filter)."""
        return event_type not in self._SKIP_TYPES


# ── SSEHandler ───────────────────────────────────────────


class SSEHandler:
    """Pushes events to SSE send_cb for frontend consumption.

    Filter rules migrated from _should_skip_push in agent_run_service.py:
    - Skip: assistant_state (internal-only)
    - Skip: Planner source streaming thought (ephemeral JSON)
    - Skip: direct mode non-streaming complete thought (persist-only)
    - Push: everything else

    Supports both async (loop present) and sync (worker mode) send_cb.
    """

    def __init__(
        self,
        send_cb: Callable,
        loop: asyncio.AbstractEventLoop | None,
        session_id: str,
        task_id: str,
        invocation_id: str | None,
        mode: str,
    ) -> None:
        self._send_cb = send_cb
        self._loop = loop
        self._session_id = session_id
        self._task_id = task_id
        self._invocation_id = invocation_id
        self._mode = mode
        self._is_async = asyncio.iscoroutinefunction(send_cb)

    def handle(self, event: BusEvent) -> None:  # type: ignore[arg-type]
        """Push event to SSE if it passes filter rules."""
        if self._should_skip(event):
            return

        payload = event.model_dump(mode="json")
        content = _public_content_for_event(str(payload.get("type", "")), payload)
        if content is not None:
            payload["content"] = content
        payload["source"] = _normalize_public_source(payload.get("source"))
        payload["session_id"] = self._session_id
        payload["task_id"] = self._task_id
        if self._invocation_id is not None:
            payload["invocation_id"] = self._invocation_id

        self._send(payload)

    def _should_skip(self, event: BusEvent) -> bool:  # type: ignore[arg-type]
        """Check if event should be skipped for SSE push.

        Migrated from _should_skip_push in agent_run_service.py.
        """
        event_type = getattr(event, "type", "")

        # Internal-only: never push assistant_state to frontend
        if event_type == "assistant_state":
            return True

        if isinstance(event, ThoughtEvent):
            is_streaming = event.stream_state in ("start", "streaming", "end")

            # Planner streaming thoughts are internal JSON -- skip push
            if event.source == "Planner" and is_streaming:
                return True

            # Direct mode: non-streaming complete thoughts are persist-only
            if self._mode == "direct" and not is_streaming:
                return True

        return False

    def _send(self, payload: dict[str, Any]) -> None:
        """Send payload via sync or async path."""
        if self._loop is not None and self._is_async:
            future = asyncio.run_coroutine_threadsafe(
                self._send_cb(payload), self._loop
            )
            try:
                future.result(timeout=5)
            except Exception:
                logger.warning(
                    "SSE send_cb timeout or error session_id=%s type=%s",
                    self._session_id,
                    payload.get("type"),
                    exc_info=True,
                )
        else:
            self._send_cb(payload)
