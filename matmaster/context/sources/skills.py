from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from matmaster.context.ports import ActiveSkill
from matmaster.context.sections import ALL_VIEWS, ContextSection, SectionOrder


def format_loaded_skills(skills: Iterable[ActiveSkill]) -> str:
    skill_tuple = tuple(skills)
    if not skill_tuple:
        return ""
    lines = ["[Loaded skills]"]
    for skill in skill_tuple:
        suffix = f" (mcp_server={skill.mcp_server})" if skill.mcp_server else ""
        if skill.description:
            lines.append(f"- {skill.name}: {skill.description}{suffix}")
        else:
            lines.append(f"- {skill.name}{suffix}")
    return "\n".join(lines)


@dataclass(frozen=True)
class SessionSkillsSource:
    skills: tuple[ActiveSkill, ...] = ()

    @classmethod
    def from_skills(cls, skills: Iterable[ActiveSkill]) -> SessionSkillsSource:
        return cls(skills=tuple(skills))

    def to_sections(self) -> tuple[ContextSection, ...]:
        text = format_loaded_skills(self.skills)
        if not text:
            return ()
        return (
            ContextSection(
                key="session-skills",
                tag="loaded-skills",
                content=text,
                order=SectionOrder.SESSION_SKILLS,
                views=ALL_VIEWS,
            ),
        )
