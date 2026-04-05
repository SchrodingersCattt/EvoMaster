"""SSEHandler -- pushes events to SSE send_cb for frontend consumption.

Filter rules migrated from _should_skip_push in agent_run_service.py:
- Skip: assistant_state (internal-only)
- Skip: Planner source streaming thought (ephemeral JSON)
- Skip: direct mode non-streaming complete thought (persist-only)
- Push: everything else

Pure async handler -- send_cb is always awaited.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from matmaster.integration.event_payloads import build_public_sse_payload_from_bus_dump
from matmaster.response_text import is_trivial_response_text
from matmaster.types.events import BusEvent, ResponseEvent, ThoughtEvent

logger = logging.getLogger(__name__)


class SSEHandler:
    """Pushes events to SSE send_cb for frontend consumption.

    Filter rules migrated from _should_skip_push in agent_run_service.py:
    - Skip: assistant_state (internal-only)
    - Skip: Planner source streaming thought (ephemeral JSON)
    - Skip: direct mode non-streaming complete thought (persist-only)
    - Push: everything else

    Pure async handler -- send_cb is always awaited.
    """

    def __init__(
        self,
        send_cb: Callable[..., Coroutine[Any, Any, Any] | Any],
        session_id: str,
        task_id: str,
        invocation_id: str | None,
        mode: str,
    ) -> None:
        self._send_cb = send_cb
        self._session_id = session_id
        self._task_id = task_id
        self._invocation_id = invocation_id
        self._mode = mode
        self._pending_trivial_response: dict[tuple[str | None, str], str] = {}

    async def handle(self, event: BusEvent) -> None:
        """Push event to SSE if it passes filter rules."""
        if isinstance(event, ResponseEvent):
            if await self._handle_response_event(event):
                return
        elif getattr(event, 'type', '') in {'assistant_state', 'tool_call'}:
            self._clear_pending_for_spawn(getattr(event, 'spawn_id', None))

        if self._should_skip(event):
            return

        await self._emit_event(event)

    async def _handle_response_event(self, event: ResponseEvent) -> bool:
        """Buffer punctuation-only prefixes until we know they are user-visible."""
        key = self._response_buffer_key(event)

        if event.stream_state == 'streaming' and is_trivial_response_text(
            event.content
        ):
            self._pending_trivial_response[key] = (
                self._pending_trivial_response.get(key, '') + event.content
            )
            return True

        if event.stream_state == 'streaming':
            pending = self._pending_trivial_response.pop(key, '')
            if pending:
                await self._emit_event(event.model_copy(update={'content': pending}))
            if self._should_skip(event):
                return True
            await self._emit_event(event)
            return True

        if event.stream_state == 'end':
            self._pending_trivial_response.pop(key, None)

        return False

    def _response_buffer_key(self, event: ResponseEvent) -> tuple[str | None, str]:
        """Group buffered trivial chunks by spawn + response stream."""
        return (getattr(event, 'spawn_id', None), event.stream_id or '__default__')

    def _clear_pending_for_spawn(self, spawn_id: str | None) -> None:
        """Drop buffered prefixes once the run switches from response to tool use."""
        self._pending_trivial_response = {
            key: value
            for key, value in self._pending_trivial_response.items()
            if key[0] != spawn_id
        }

    async def _emit_event(self, event: BusEvent) -> None:
        """Serialize and forward a single event to send_cb."""
        raw = event.model_dump(mode='json')
        payload = build_public_sse_payload_from_bus_dump(
            raw,
            session_id=self._session_id,
            task_id=self._task_id,
            invocation_id=self._invocation_id,
            spawn_id=getattr(event, 'spawn_id', None),
        )
        result = self._send_cb(payload)
        if inspect.isawaitable(result):
            await result

    def _should_skip(self, event: BusEvent) -> bool:
        """Check if event should be skipped for SSE push.

        Migrated from _should_skip_push in agent_run_service.py.
        """
        event_type = getattr(event, 'type', '')

        if (
            isinstance(event, (ThoughtEvent, ResponseEvent))
            and event.stream_state == 'complete'
        ):
            return True

        # Internal-only: never push assistant_state to frontend
        if event_type == 'assistant_state':
            return True

        # skill_hit is persist-only, not pushed to frontend
        if event_type == 'skill_hit':
            return True

        if isinstance(event, ThoughtEvent):
            is_streaming = event.stream_state in ('start', 'streaming', 'end')

            # Planner streaming thoughts are internal JSON -- skip push
            if event.source == 'Planner' and is_streaming:
                return True

            # Direct mode: non-streaming complete thoughts are persist-only
            if self._mode == 'direct' and not is_streaming:
                return True

        return False
