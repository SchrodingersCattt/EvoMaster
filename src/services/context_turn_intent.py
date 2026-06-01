from __future__ import annotations

from matmaster.context.turn_intent import (
    _latest_anchor_hash_from_events,
    resolve_turn_intent,
)


async def resolve_turn_context_intent(
    *,
    instructions_hash: str,
    session_id: str,
    spawn_id: str | None,
    events_port,
):
    resolution = await resolve_turn_intent(
        instructions_hash=instructions_hash,
        session_id=session_id,
        spawn_id=spawn_id,
        events_port=events_port,
    )
    return resolution.intent
