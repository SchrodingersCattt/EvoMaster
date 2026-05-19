from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from matmaster.context.ports import (
    SessionEvent,
    SessionEventQuery,
    SessionJobs,
    SessionJobsQuery,
    UserInstructions,
)
from src.services.session_event_codec import decode_session_events
from src.services.user_turn_context_service import (
    USER_INSTRUCTIONS_MAX_BYTES,
    hash_user_instructions,
    truncate_utf8,
)

logger = logging.getLogger(__name__)


class AppUserInstructionsPort:
    async def load_user_instructions(
        self,
        workspace_root: Path,
    ) -> UserInstructions:
        path = workspace_root / ".matmaster" / "AGENT.md"
        try:
            raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except FileNotFoundError:
            return UserInstructions(
                text="",
                hash=hash_user_instructions(""),
                truncated=False,
            )
        # Other IO / decoding errors intentionally propagate. Missing AGENT.md
        # is normal; unreadable or invalid files should not be silently ignored.

        text, truncated = truncate_utf8(raw, USER_INSTRUCTIONS_MAX_BYTES)
        if truncated:
            logger.warning(
                "AGENT.md exceeds %d bytes; truncating user instructions",
                USER_INSTRUCTIONS_MAX_BYTES,
            )
        return UserInstructions(
            text=text,
            hash=hash_user_instructions(text),
            truncated=truncated,
        )


class AppSessionEventsPort:
    def __init__(self, events_table: object) -> None:
        self._events_table = events_table

    async def load_events(
        self,
        query: SessionEventQuery,
    ) -> tuple[SessionEvent, ...]:
        rows = await asyncio.to_thread(
            self._events_table.query_context_events,
            session_id=query.session_id,
            spawn_id=query.spawn_id,
            until_event_id=query.until_event_id,
            event_types=query.event_types,
            limit=query.limit,
            order=query.order,
        )
        return decode_session_events(rows)


class AppSessionJobsPort:
    async def load_session_jobs(
        self,
        query: SessionJobsQuery,
    ) -> SessionJobs:
        return SessionJobs.empty()
