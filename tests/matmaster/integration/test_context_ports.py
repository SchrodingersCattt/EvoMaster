from __future__ import annotations

from matmaster.context.ports import (
    SessionEvent,
    SessionEventQuery,
    SessionJobs,
    SessionJobsQuery,
)
from src.services.session_event_codec import decode_session_events


class TableSessionEventsPort:
    def __init__(self, events_table: object) -> None:
        self._events_table = events_table

    async def load_events(
        self,
        query: SessionEventQuery,
    ) -> tuple[SessionEvent, ...]:
        rows = self._events_table.query_context_events(
            session_id=query.session_id,
            spawn_id=query.spawn_id,
            until_event_id=query.until_event_id,
            event_types=query.event_types,
            limit=query.limit,
            order=query.order,
        )
        return decode_session_events(rows)


class EmptySessionJobsPort:
    async def load_session_jobs(
        self,
        query: SessionJobsQuery,
    ) -> SessionJobs:
        return SessionJobs.empty()
