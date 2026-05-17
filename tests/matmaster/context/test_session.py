from __future__ import annotations

from pathlib import Path

import pytest

from matmaster.context.ports import SessionEvent
from matmaster.context.session import SessionContextBuilder
from matmaster.skills.registry import SkillRegistry


def _registry(tmp_path: Path) -> SkillRegistry:
    root = tmp_path / "skills"
    skill_dir = root / "pxrd"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: pxrd\ndescription: PXRD helper\nmcp_server: mat_xrd\n---\nbody\n",
        encoding="utf-8",
    )
    return SkillRegistry([root])


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


def test_build_sections_returns_attachments_skills_tools_in_order(
    tmp_path: Path,
) -> None:
    builder = SessionContextBuilder(
        events=_session_events(_BASE_EVENTS),
        skill_registry=_registry(tmp_path),
        legal_mcp_servers={"mat_xrd"},
        schemas_by_server={"mat_xrd": [{"name": "read"}]},
    )

    sections = builder.build_sections(until_event_id=None, include_attachments=True)

    keys = tuple(section.key for section in sections)
    assert "session_skills" in keys
    assert "session_tools" in keys
    assert "session_attachments" in keys


def test_build_sections_until_event_id_truncates_attachments(
    tmp_path: Path,
) -> None:
    builder = SessionContextBuilder(
        events=_session_events(_BASE_EVENTS),
        skill_registry=_registry(tmp_path),
        legal_mcp_servers={"mat_xrd"},
        schemas_by_server={"mat_xrd": [{"name": "read"}]},
    )

    sections = builder.build_sections(until_event_id=10, include_attachments=True)
    attachments = next(s for s in sections if s.key == "session_attachments")

    assert "a.csv" in attachments.content
    assert "b.csv" not in attachments.content


def test_build_sections_exclude_attachments_drops_section(
    tmp_path: Path,
) -> None:
    builder = SessionContextBuilder(
        events=_session_events(_BASE_EVENTS),
        skill_registry=_registry(tmp_path),
        legal_mcp_servers={"mat_xrd"},
        schemas_by_server={"mat_xrd": [{"name": "read"}]},
    )

    sections = builder.build_sections(until_event_id=None, include_attachments=False)

    keys = tuple(section.key for section in sections)
    assert "session_attachments" not in keys


def test_build_sections_empty_inputs_returns_empty_tuple(tmp_path: Path) -> None:
    builder = SessionContextBuilder(
        events=(),
        skill_registry=_registry(tmp_path),
        legal_mcp_servers=None,
        schemas_by_server=None,
    )

    sections = builder.build_sections(until_event_id=None, include_attachments=True)

    assert sections == ()


def test_constructor_rejects_list_input_to_enforce_typed_envelope(
    tmp_path: Path,
) -> None:
    with pytest.raises(TypeError, match="tuple"):
        SessionContextBuilder(
            events=list(_session_events(_BASE_EVENTS)),  # type: ignore[arg-type]
            skill_registry=_registry(tmp_path),
            legal_mcp_servers=None,
            schemas_by_server=None,
        )


def test_sections_are_in_section_order_after_render_sort(tmp_path: Path) -> None:
    builder = SessionContextBuilder(
        events=_session_events(_BASE_EVENTS),
        skill_registry=_registry(tmp_path),
        legal_mcp_servers={"mat_xrd"},
        schemas_by_server={"mat_xrd": [{"name": "read"}]},
    )

    sections = builder.build_sections(until_event_id=None, include_attachments=True)
    orders = [section.order for section in sections]
    assert orders == sorted(
        orders
    ), "SessionContextBuilder should emit sections in SectionOrder ascending order"
