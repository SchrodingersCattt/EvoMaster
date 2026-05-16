from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from matmaster.context.ports import (
    JsonObject,
    JsonValue,
    SessionEvent,
    SessionEventQuery,
    SessionJobs,
    SessionJobsQuery,
    UserInstructions,
)
from src.services.user_turn_context_service import (
    USER_INSTRUCTIONS_MAX_BYTES,
    hash_user_instructions,
    truncate_utf8,
)

logger = logging.getLogger(__name__)


def _freeze_json_value(value: Any) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _freeze_json_value(inner) for key, inner in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_json_value(inner) for inner in value)
    raise TypeError(
        f"Unsupported JSON value type in context event payload: {type(value)!r}"
    )


def _freeze_json_object(value: Any) -> JsonObject:
    if not isinstance(value, Mapping):
        return {"value": _freeze_json_value(value)}
    return {str(key): _freeze_json_value(inner) for key, inner in value.items()}


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
        return tuple(self._row_to_event(row) for row in rows)

    @staticmethod
    def _row_to_event(row: Mapping[str, Any]) -> SessionEvent:
        raw_content = row["content"] if "content" in row else {}
        return SessionEvent(
            id=int(row.get("id") or 0),
            event_type=str(row.get("type") or row.get("event_type") or ""),
            source=row.get("source"),
            content=_freeze_json_object(raw_content),
            task_id=row.get("task_id"),
            invocation_id=row.get("invocation_id"),
            spawn_id=row.get("spawn_id"),
        )


class AppSessionJobsPort:
    async def load_session_jobs(
        self,
        query: SessionJobsQuery,
    ) -> SessionJobs:
        return SessionJobs.empty()
