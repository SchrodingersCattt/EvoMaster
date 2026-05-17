from __future__ import annotations

from matmaster.context.ports import ActiveSkill, SessionEvent
from matmaster.core.runtime_context_assembly import (
    build_session_context_factory,
    empty_skill_resolver,
)


def test_build_session_context_factory_invokes_resolver_per_call() -> None:
    captured: list[tuple[SessionEvent, ...]] = []

    def resolver(events: tuple[SessionEvent, ...]) -> tuple[ActiveSkill, ...]:
        captured.append(events)
        return (ActiveSkill(name="pxrd"),)

    factory = build_session_context_factory(
        skill_resolver=resolver,
        legal_mcp_servers=None,
        schemas_by_server=None,
    )
    events = (SessionEvent(id=1, event_type="query", source="User", content={}),)
    builder = factory(events)

    assert captured == [events]
    assert builder.active_skills == (ActiveSkill(name="pxrd"),)


def test_empty_skill_resolver_returns_empty_tuple() -> None:
    assert empty_skill_resolver(()) == ()
    assert (
        empty_skill_resolver(
            (
                SessionEvent(
                    id=1,
                    event_type="skill_hit",
                    source=None,
                    content={"skill_name": "x"},
                ),
            )
        )
        == ()
    )
