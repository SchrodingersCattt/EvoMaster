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
    ) -> Callable[..., Awaitable[int | None]]:
        async def sink(
            *,
            payload: dict[str, Any],
            base_messages: list[dict[str, Any]],
        ) -> int | None:
            if payload.get("durability") != "durable":
                return None

            if (
                payload.get("schema_version") == "history_checkpoint.v1"
                and payload.get("covered_until_event_id") is None
            ):
                raise ValueError(
                    "history_checkpoint.v1 requires covered_until_event_id"
                )

            validate_base_messages(deserialize_base_messages(base_messages))
            await fanout.flush_persistence_barrier()
            raw_covered_until = payload.get("covered_until_event_id")
            if raw_covered_until is not None:
                covered_until_event_id = int(raw_covered_until)
            else:
                covered_until_event_id = await asyncio.to_thread(
                    self.events_table.get_latest_scope_event_id,
                    session_id,
                    spawn_id,
                )
            checkpoint_kwargs = {
                key: payload[key]
                for key in (
                    "schema_version",
                    "render_version",
                    "user_instructions_text",
                    "user_instructions_hash",
                )
                if key in payload
            }
            await asyncio.to_thread(
                self.events_table.add_history_checkpoint,
                session_id,
                task_id=task_id,
                invocation_id=invocation_id,
                spawn_id=spawn_id,
                covered_until_event_id=covered_until_event_id,
                base_messages=base_messages,
                reason=str(payload.get("strategy") or "summary"),
                **checkpoint_kwargs,
            )
            return covered_until_event_id

        return sink
