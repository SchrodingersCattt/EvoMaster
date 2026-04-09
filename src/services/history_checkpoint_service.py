from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from src.services.history_checkpoint_codec import (
    deserialize_base_messages,
    validate_base_messages,
)


class HistoryCheckpointService:
    def __init__(self, events_table: Any) -> None:
        self.events_table = events_table

    def build_checkpoint_sink(
        self,
        *,
        fanout: Any,
        session_id: str,
        task_id: str | None,
        invocation_id: str | None,
        spawn_id: str | None,
    ) -> Callable[..., Awaitable[None]]:
        async def sink(
            *,
            payload: dict[str, Any],
            base_messages: list[dict[str, Any]],
        ) -> None:
            if payload.get("durability") != "durable":
                return

            validate_base_messages(deserialize_base_messages(base_messages))
            await fanout.flush_persistence_barrier()
            covered_until_event_id = await asyncio.to_thread(
                self.events_table.get_latest_scope_event_id,
                session_id,
                spawn_id,
            )
            await asyncio.to_thread(
                self.events_table.add_checkpoint_pair,
                session_id,
                task_id=task_id,
                invocation_id=invocation_id,
                spawn_id=spawn_id,
                covered_until_event_id=covered_until_event_id,
                base_messages=base_messages,
                reason=str(payload.get("strategy") or "summary"),
            )

        return sink
