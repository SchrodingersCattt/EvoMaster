from __future__ import annotations

from dataclasses import dataclass

from matmaster.context.assembly import ContextAssemblyIntent
from matmaster.context.ports import SessionEvent, SessionEventQuery, SessionEventsPort
from matmaster.context.scanner import scan_skill_hits

DEFAULT_INTENT_EVENT_LIMIT = 50
DEFAULT_ACTIVE_SKILL_EVENT_LIMIT = 500


@dataclass(frozen=True)
class TurnIntentResolution:
    intent: ContextAssemblyIntent
    active_skills: frozenset[str] = frozenset()


def decide_turn_context_intent(
    *,
    current_hash: str,
    latest_anchor_hash: str | None,
) -> ContextAssemblyIntent:
    if latest_anchor_hash is None or latest_anchor_hash != current_hash:
        return ContextAssemblyIntent.ANCHOR_TURN
    return ContextAssemblyIntent.CONTINUATION_TURN


async def resolve_turn_intent(
    *,
    events_port: SessionEventsPort,
    instructions_hash: str,
    session_id: str,
    spawn_id: str | None,
    active_skill_event_limit: int = DEFAULT_ACTIVE_SKILL_EVENT_LIMIT,
) -> TurnIntentResolution:
    intent_events = await events_port.load_events(
        SessionEventQuery(
            session_id=session_id,
            spawn_id=spawn_id,
            event_types=("user_turn_context", "history_checkpoint"),
            limit=DEFAULT_INTENT_EVENT_LIMIT,
            order="desc",
        )
    )
    latest_hash = _latest_anchor_hash_from_events(intent_events)
    skill_events = await events_port.load_events(
        SessionEventQuery(
            session_id=session_id,
            spawn_id=spawn_id,
            event_types=("skill_hit",),
            limit=active_skill_event_limit,
            order="asc",
        )
    )
    active_skills = frozenset(
        record.skill_name
        for record in scan_skill_hits(skill_events)
        if record.skill_name
    )
    return TurnIntentResolution(
        intent=decide_turn_context_intent(
            current_hash=instructions_hash,
            latest_anchor_hash=latest_hash,
        ),
        active_skills=active_skills,
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
