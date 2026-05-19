from __future__ import annotations

import pytest

from matmaster.context.assembly import ContextAssemblyIntent
from matmaster.context.ports import SessionEvent
from src.services.context_turn_intent import (
    _latest_anchor_hash_from_events,
    resolve_turn_context_intent,
)


class EventsPort:
    def __init__(self, events):
        self.events = events
        self.queries = []

    async def load_events(self, query):
        self.queries.append(query)
        return tuple(self.events)


def _event(event_type: str, content: dict, event_id: int) -> SessionEvent:
    return SessionEvent(
        id=event_id,
        event_type=event_type,
        source="MatMaster",
        content=content,
    )


def test_latest_anchor_hash_stops_at_checkpoint_without_hash() -> None:
    events = (
        _event("history_checkpoint", {"covered_until_event_id": 30}, 31),
        _event(
            "user_turn_context",
            {"kind": "anchor", "user_instructions_hash": "sha256:old"},
            30,
        ),
    )

    assert _latest_anchor_hash_from_events(events) is None


def test_latest_anchor_hash_uses_checkpoint_hash_as_barrier_value() -> None:
    events = (
        _event(
            "history_checkpoint",
            {"covered_until_event_id": 30, "user_instructions_hash": "sha256:cp"},
            31,
        ),
        _event(
            "user_turn_context",
            {"kind": "anchor", "user_instructions_hash": "sha256:old"},
            30,
        ),
    )

    assert _latest_anchor_hash_from_events(events) == "sha256:cp"


def test_latest_anchor_hash_skips_continuation_until_anchor() -> None:
    events = (
        _event("user_turn_context", {"kind": "continuation"}, 33),
        _event(
            "user_turn_context",
            {"kind": "anchor", "user_instructions_hash": "sha256:anchor"},
            32,
        ),
    )

    assert _latest_anchor_hash_from_events(events) == "sha256:anchor"


@pytest.mark.asyncio
async def test_resolve_turn_context_intent_queries_recent_internal_events() -> None:
    port = EventsPort(
        [
            _event(
                "user_turn_context",
                {"kind": "anchor", "user_instructions_hash": "sha256:same"},
                10,
            )
        ]
    )

    intent = await resolve_turn_context_intent(
        instructions_hash="sha256:same",
        session_id="sess-1",
        spawn_id=None,
        events_port=port,
    )

    assert intent == ContextAssemblyIntent.CONTINUATION_TURN
    assert port.queries[0].session_id == "sess-1"
    assert port.queries[0].event_types == (
        "user_turn_context",
        "history_checkpoint",
    )
    assert port.queries[0].limit == 50
    assert port.queries[0].order == "desc"


@pytest.mark.asyncio
async def test_resolve_turn_context_intent_returns_anchor_when_no_events() -> None:
    intent = await resolve_turn_context_intent(
        instructions_hash="sha256:current",
        session_id="sess-1",
        spawn_id=None,
        events_port=EventsPort(()),
    )

    assert intent == ContextAssemblyIntent.ANCHOR_TURN


@pytest.mark.asyncio
async def test_resolve_turn_context_intent_returns_anchor_when_hash_differs() -> None:
    port = EventsPort(
        [
            _event(
                "user_turn_context",
                {"kind": "anchor", "user_instructions_hash": "sha256:old"},
                10,
            )
        ]
    )

    intent = await resolve_turn_context_intent(
        instructions_hash="sha256:new",
        session_id="sess-1",
        spawn_id=None,
        events_port=port,
    )

    assert intent == ContextAssemblyIntent.ANCHOR_TURN


@pytest.mark.asyncio
async def test_resolve_turn_context_intent_uses_checkpoint_hash() -> None:
    port = EventsPort(
        [
            _event(
                "history_checkpoint",
                {"covered_until_event_id": 30, "user_instructions_hash": "sha256:cp"},
                31,
            )
        ]
    )

    intent = await resolve_turn_context_intent(
        instructions_hash="sha256:cp",
        session_id="sess-1",
        spawn_id=None,
        events_port=port,
    )

    assert intent == ContextAssemblyIntent.CONTINUATION_TURN


@pytest.mark.asyncio
async def test_resolve_turn_context_intent_falls_back_to_anchor_without_recent_anchor() -> None:
    port = EventsPort(
        [
            _event("user_turn_context", {"kind": "continuation"}, event_id)
            for event_id in range(50, 0, -1)
        ]
    )

    intent = await resolve_turn_context_intent(
        instructions_hash="sha256:current",
        session_id="sess-1",
        spawn_id=None,
        events_port=port,
    )

    assert intent == ContextAssemblyIntent.ANCHOR_TURN
