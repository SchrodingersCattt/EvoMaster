from __future__ import annotations

from matmaster.context.ports import ActiveSkill
from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.context.sources.skills import (
    SessionSkillsSource,
    format_loaded_skills,
)


def test_format_loaded_skills_omits_section_when_empty() -> None:
    assert format_loaded_skills(()) == ""


def test_format_loaded_skills_renders_name_description_and_mcp_server() -> None:
    skills = (
        ActiveSkill(name="pxrd", description="X-ray powder", mcp_server="xrd_srv"),
        ActiveSkill(name="mlip", description=""),
    )

    rendered = format_loaded_skills(skills)

    assert "[Loaded skills]" in rendered
    assert "- pxrd: X-ray powder (mcp_server=xrd_srv)" in rendered
    assert "- mlip" in rendered
    assert "mcp_server=" not in rendered.splitlines()[-1]


def test_session_skills_source_from_skills_round_trips() -> None:
    skills = (ActiveSkill(name="pxrd"),)

    source = SessionSkillsSource.from_skills(skills)
    sections = source.to_sections()

    assert source.skills == skills
    assert len(sections) == 1
    section = sections[0]
    assert isinstance(section, ContextSection)
    assert section.key == "session-skills"
    assert section.tag == "loaded-skills"
    assert section.order == SectionOrder.SESSION_SKILLS
    assert (
        ContextView.RUNTIME in section.views and ContextView.CHECKPOINT in section.views
    )


def test_session_skills_source_empty() -> None:
    source = SessionSkillsSource.from_skills(())

    assert source.to_sections() == ()
