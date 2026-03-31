"""SSEHandler -- pushes events to SSE send_cb for frontend consumption.

Filter rules migrated from _should_skip_push in agent_run_service.py:
- Skip: assistant_state (internal-only)
- Skip: Planner source streaming thought (ephemeral JSON)
- Skip: direct mode non-streaming complete thought (persist-only)
- Push: everything else

Pure async handler -- send_cb is always awaited.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from matmaster.integration.event_payloads import build_public_sse_payload_from_bus_dump
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

    async def handle(self, event: BusEvent) -> None:
        """Push event to SSE if it passes filter rules."""
        if self._should_skip(event):
            return

        raw = event.model_dump(mode='json')
        payload = build_public_sse_payload_from_bus_dump(
            raw,
            session_id=self._session_id,
            task_id=self._task_id,
            invocation_id=self._invocation_id,
            spawn_id=getattr(event, 'spawn_id', None),
        )
        await self._send_cb(payload)

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
