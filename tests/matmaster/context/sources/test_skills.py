from __future__ import annotations

from pathlib import Path

from matmaster.context.ports import SessionEvent
from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.context.sources.skills import (
    SessionSkillsSource,
    format_loaded_skills,
    resolve_active_skills,
    skill_name,
)
from matmaster.skills.registry import SkillRegistry


def _registry(tmp_path: Path) -> SkillRegistry:
    root = tmp_path / "skills"
    skill_dir = root / "pxrd"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: pxrd\ndescription: PXRD helper\nmcp_server: mat_xrd\n---\nbody\n",
        encoding="utf-8",
    )
    other_dir = root / "mlip"
    other_dir.mkdir(parents=True)
    (other_dir / "SKILL.md").write_text(
        "---\nname: mlip\ndescription: MLIP runner\nmcp_server: mat_mlip\n---\nbody\n",
        encoding="utf-8",
    )
    return SkillRegistry([root])


def test_resolve_active_skills_returns_registered_skills_in_event_order(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    events = (
        SessionEvent(
            id=1, event_type="skill_hit", source=None, content={"skill_name": "pxrd"}
        ),
        SessionEvent(
            id=2, event_type="skill_hit", source=None, content={"skill_name": "mlip"}
        ),
        SessionEvent(
            id=3, event_type="skill_hit", source=None, content={"skill_name": "pxrd"}
        ),
    )

    skills = resolve_active_skills(events, registry)

    names = tuple(skill_name(skill) for skill in skills)
    assert names == ("pxrd", "mlip")


def test_resolve_active_skills_uses_skill_hit_events_only(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    events = (
        SessionEvent(
            id=1,
            event_type="assistant_state",
            source=None,
            content={"tool_calls": ({"name": "mat_xrd_read"},)},
        ),
        SessionEvent(
            id=2,
            event_type="tool_call",
            source=None,
            content={"tool_name": "mat_xrd_read"},
        ),
        SessionEvent(
            id=3, event_type="skill_hit", source=None, content={"skill_name": "pxrd"}
        ),
        SessionEvent(
            id=4, event_type="skill_hit", source=None, content={"skill_name": "mlip"}
        ),
        SessionEvent(
            id=5,
            event_type="skill_hit",
            source=None,
            content={"skill_name": "missing"},
        ),
    )

    skills = resolve_active_skills(events, registry)

    assert [skill.meta_info.name for skill in skills] == ["pxrd", "mlip"]


def test_resolve_active_skills_handles_missing_registry_lookup(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    events = (
        SessionEvent(
            id=1,
            event_type="skill_hit",
            source=None,
            content={"skill_name": "unknown"},
        ),
    )

    skills = resolve_active_skills(events, registry)

    assert skills == ()


def test_resolve_active_skills_with_none_registry_returns_empty() -> None:
    events = (
        SessionEvent(
            id=1, event_type="skill_hit", source=None, content={"skill_name": "pxrd"}
        ),
    )

    assert resolve_active_skills(events, None) == ()


def test_format_loaded_skills_emits_legacy_header(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    events = (
        SessionEvent(
            id=1, event_type="skill_hit", source=None, content={"skill_name": "pxrd"}
        ),
    )

    text = format_loaded_skills(resolve_active_skills(events, registry))

    assert text.startswith("[Loaded skills]\n")
    assert "- pxrd: PXRD helper (mcp_server=mat_xrd)" in text


def test_session_skills_source_to_sections(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    events = (
        SessionEvent(
            id=1, event_type="skill_hit", source=None, content={"skill_name": "pxrd"}
        ),
    )

    source = SessionSkillsSource.from_events(events, skill_registry=registry)
    sections = source.to_sections()

    assert len(sections) == 1
    section = sections[0]
    assert isinstance(section, ContextSection)
    assert section.key == "session_skills"
    assert section.tag == "loaded_skills"
    assert section.order == SectionOrder.SESSION_SKILLS
    assert (
        ContextView.RUNTIME in section.views and ContextView.CHECKPOINT in section.views
    )
    assert "- pxrd: PXRD helper (mcp_server=mat_xrd)" in section.content


def test_session_skills_source_empty(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    source = SessionSkillsSource.from_events((), skill_registry=registry)
    assert source.to_sections() == ()


def test_session_skills_source_keeps_skills_for_downstream_tool_source(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    events = (
        SessionEvent(
            id=1, event_type="skill_hit", source=None, content={"skill_name": "pxrd"}
        ),
    )

    source = SessionSkillsSource.from_events(events, skill_registry=registry)

    assert source.skills != ()
    assert skill_name(source.skills[0]) == "pxrd"
