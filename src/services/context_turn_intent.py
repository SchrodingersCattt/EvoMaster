from __future__ import annotations

from matmaster.context.assembly import ContextAssemblyIntent
from matmaster.context.ports import SessionEvent, SessionEventQuery, SessionEventsPort
from matmaster.context.turn_intent import decide_turn_context_intent


async def resolve_turn_context_intent(
    *,
    instructions_hash: str,
    session_id: str,
    spawn_id: str | None,
    events_port: SessionEventsPort,
) -> ContextAssemblyIntent:
    events = await events_port.load_events(
        SessionEventQuery(
            session_id=session_id,
            spawn_id=spawn_id,
            event_types=("user_turn_context", "history_checkpoint"),
            limit=50,
            order="desc",
        )
    )
    latest_hash = _latest_anchor_hash_from_events(events)
    return decide_turn_context_intent(
        current_hash=instructions_hash,
        latest_anchor_hash=latest_hash,
    )


def _latest_anchor_hash_from_events(
    events: tuple[SessionEvent, ...],
) -> str | None:
    for event in events:
        if event.event_type == "user_turn_context":
            if event.content.get("kind") != "anchor":
                continue
            anchor_hash = event.content.get("user_instructions_hash")
            return anchor_hash if isinstance(anchor_hash, str) and anchor_hash else None
        if event.event_type == "history_checkpoint":
            checkpoint_hash = event.content.get("user_instructions_hash")
            return (
                checkpoint_hash
                if isinstance(checkpoint_hash, str) and checkpoint_hash
                else None
            )
    return None
