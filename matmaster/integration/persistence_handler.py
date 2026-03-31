"""PersistenceHandler -- persists events to database.

Filter rules migrated from _should_persist_event in agent_run_service.py:
- Skip: log_line, llm_token
- Skip: streaming ThoughtEvent / ResponseEvent deltas
- Persist: everything else
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from matmaster.integration.event_payloads import _public_content_for_event
from matmaster.types.events import BusEvent, ResponseEvent, ThoughtEvent

logger = logging.getLogger(__name__)


class PersistenceHandler:
    """Persists events to database via events_table.add_event().

    Filter rules migrated from _should_persist_event in agent_run_service.py:
    - Skip: log_line, llm_token
    - Skip: streaming ThoughtEvent / ResponseEvent deltas
    - Persist: everything else
    """

    _SKIP_TYPES = frozenset({'log_line', 'llm_token'})
    _STREAMING_STATES = frozenset({'start', 'streaming', 'end'})
    _TRIVIAL_RESPONSE_RE = re.compile(r'^[\s.。…·\-—_*]+$')

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

    async def handle(self, event: BusEvent) -> None:
        """Persist event to DB if it passes filter rules."""
        event_type = getattr(event, 'type', '')

        if not self._should_persist_type(event_type):
            return

        # Skip streaming thought/response events (ephemeral deltas)
        if (
            isinstance(event, (ThoughtEvent, ResponseEvent))
            and event.stream_state in self._STREAMING_STATES
        ):
            return

        # Skip trivial response segments (e.g. LLM emits "..." before tool calls)
        if (
            isinstance(event, ResponseEvent)
            and event.stream_state == 'complete'
            and self._TRIVIAL_RESPONSE_RE.match(event.content)
        ):
            return

        # Use the same JSON-safe payload mode as SSEHandler so persistence
        # and live SSE derive content from the same normalized field values.
        payload = event.model_dump(mode='json')
        content = _public_content_for_event(event_type, payload)

        try:
            await asyncio.to_thread(
                self._events_table.add_event,
                self._session_id,
                event.source,
                event_type,
                content,
                task_id=self._task_id,
                invocation_id=self._invocation_id,
                spawn_id=getattr(event, 'spawn_id', None),
            )
        except Exception:
            logger.error(
                'Failed to persist event type=%s session_id=%s',
                event_type,
                self._session_id,
                exc_info=True,
            )

    def _should_persist_type(self, event_type: str) -> bool:
        """Check if event type should be persisted (type-level filter)."""
        return event_type not in self._SKIP_TYPES
