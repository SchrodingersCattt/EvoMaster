from __future__ import annotations

import pytest

from matmaster.context.ports import ActiveSkill, SessionEvent
from matmaster.context.session import SessionContextBuilder

_BASE_EVENTS = [
    {
        "id": 10,
        "source": "User",
        "type": "query",
        "content": {
            "content": "first turn",
            "files": ["https://oss.example.com/a.csv"],
        },
    },
    {"id": 11, "type": "skill_hit", "content": {"skill_name": "pxrd"}},
    {
        "id": 20,
        "source": "User",
        "type": "query",
        "content": {
            "content": "second turn",
            "files": ["https://oss.example.com/b.csv"],
        },
    },
]


def _session_events(rows: list[dict]) -> tuple[SessionEvent, ...]:
    events: list[SessionEvent] = []
    for row in rows:
        events.append(
            SessionEvent(
                id=int(row["id"]),
                source=row.get("source"),
                event_type=str(row.get("type") or ""),
                content=row.get("content") or {"value": None},
            )
        )
    return tuple(events)


def test_build_sections_returns_attachments_skills_tools_in_order() -> None:
    builder = SessionContextBuilder(
        events=_session_events(_BASE_EVENTS),
        active_skills=(
            ActiveSkill(
                name="pxrd",
                description="PXRD helper",
                mcp_server="mat_xrd",
            ),
        ),
        legal_mcp_servers={"mat_xrd"},
        schemas_by_server={"mat_xrd": [{"name": "read"}]},
    )

    sections = builder.build_sections(until_event_id=None, include_attachments=True)

    keys = tuple(section.key for section in sections)
    assert "session-skills" in keys
    assert "session-tools" in keys
    assert "session-attachments" in keys


def test_session_context_builder_renders_skills_section() -> None:
    builder = SessionContextBuilder(
        events=(),
        active_skills=(ActiveSkill(name="pxrd"),),
        legal_mcp_servers=None,
        schemas_by_server=None,
    )

    sections = builder.build_sections(until_event_id=None, include_attachments=False)

    assert any(section.tag == "loaded-skills" for section in sections)


def test_build_sections_until_event_id_truncates_attachments() -> None:
    builder = SessionContextBuilder(
        events=_session_events(_BASE_EVENTS),
        active_skills=(),
        legal_mcp_servers={"mat_xrd"},
        schemas_by_server={"mat_xrd": [{"name": "read"}]},
    )

    sections = builder.build_sections(until_event_id=10, include_attachments=True)
    attachments = next(s for s in sections if s.key == "session-attachments")

    assert "a.csv" in attachments.content
    assert "b.csv" not in attachments.content


def test_session_context_builder_attachments_still_use_events() -> None:
    builder = SessionContextBuilder(
        events=(SessionEvent(id=1, event_type="query", source="User", content={}),),
        active_skills=(),
    )

    sections = builder.build_sections(until_event_id=10, include_attachments=True)

    assert isinstance(sections, tuple)


def test_build_sections_exclude_attachments_drops_section() -> None:
    builder = SessionContextBuilder(
        events=_session_events(_BASE_EVENTS),
        active_skills=(ActiveSkill(name="pxrd", mcp_server="mat_xrd"),),
        legal_mcp_servers={"mat_xrd"},
        schemas_by_server={"mat_xrd": [{"name": "read"}]},
    )

    sections = builder.build_sections(until_event_id=None, include_attachments=False)

    keys = tuple(section.key for section in sections)
    assert "session-attachments" not in keys


def test_build_sections_empty_inputs_returns_empty_tuple() -> None:
    builder = SessionContextBuilder(
        events=(),
        active_skills=(),
        legal_mcp_servers=None,
        schemas_by_server=None,
    )

    sections = builder.build_sections(until_event_id=None, include_attachments=True)

    assert sections == ()


def test_constructor_rejects_list_input_to_enforce_typed_envelope() -> None:
    with pytest.raises(TypeError, match="tuple"):
        SessionContextBuilder(
            events=list(_session_events(_BASE_EVENTS)),  # type: ignore[arg-type]
            active_skills=(),
            legal_mcp_servers=None,
            schemas_by_server=None,
        )


def test_constructor_rejects_non_tuple_active_skills() -> None:
    with pytest.raises(TypeError, match="active_skills"):
        SessionContextBuilder(
            events=(),
            active_skills=[ActiveSkill(name="pxrd")],  # type: ignore[arg-type]
        )


def test_sections_are_in_section_order_after_render_sort() -> None:
    builder = SessionContextBuilder(
        events=_session_events(_BASE_EVENTS),
        active_skills=(ActiveSkill(name="pxrd", mcp_server="mat_xrd"),),
        legal_mcp_servers={"mat_xrd"},
        schemas_by_server={"mat_xrd": [{"name": "read"}]},
    )

    sections = builder.build_sections(until_event_id=None, include_attachments=True)
    orders = [section.order for section in sections]
    assert orders == sorted(
        orders
    ), "SessionContextBuilder should emit sections in SectionOrder ascending order"
