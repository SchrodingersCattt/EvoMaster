"""SSEHandler -- pushes events to SSE send_cb for frontend consumption.

Filter rules migrated from _should_skip_push in agent_run_service.py:
- Skip: assistant_state (internal-only)
- Skip: Planner source streaming thought (ephemeral JSON)
- Skip: direct mode non-streaming complete thought (persist-only)
- Push: everything else

Supports both async (loop present) and sync (worker mode) send_cb.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from matmaster.integration.event_payloads import (
    _normalize_public_source,
    _public_content_for_event,
)
from matmaster.types.events import BusEvent, ThoughtEvent

logger = logging.getLogger(__name__)


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
