from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from matmaster.context.ports import SessionEvent
from matmaster.context.scanner import scan_skill_hits
from matmaster.context.sections import ALL_VIEWS, ContextSection, SectionOrder


def skill_name(skill: Any) -> str:
    return str(
        getattr(skill, "name", "")
        or getattr(getattr(skill, "meta_info", None), "name", "")
    ).strip()


def resolve_active_skills(
    events: Iterable[SessionEvent],
    skill_registry: Any,
) -> tuple[Any, ...]:
    if skill_registry is None:
        return ()
    resolved: list[Any] = []
    for record in scan_skill_hits(events):
        try:
            skill = skill_registry.get_skill(record.skill_name)
        except Exception:
            continue
        if skill is not None:
            resolved.append(skill)
    return tuple(resolved)


def format_loaded_skills(skills: Iterable[Any]) -> str:
    skill_tuple = tuple(skills)
    if not skill_tuple:
        return ""
    lines = ["[Loaded skills]"]
    for skill in skill_tuple:
        name = skill_name(skill)
        meta = getattr(skill, "meta_info", None)
        description = getattr(meta, "description", "") or ""
        mcp_server = getattr(meta, "mcp_server", None)
        suffix = f" (mcp_server={mcp_server})" if mcp_server else ""
        if description:
            lines.append(f"- {name}: {description}{suffix}")
        else:
            lines.append(f"- {name}{suffix}")
    return "\n".join(lines)


@dataclass(frozen=True)
class SessionSkillsSource:
    skills: tuple[Any, ...] = ()

    @classmethod
    def from_events(
        cls,
        events: Iterable[SessionEvent],
        *,
        skill_registry: Any,
    ) -> SessionSkillsSource:
        return cls(skills=resolve_active_skills(events, skill_registry))

    def to_sections(self) -> tuple[ContextSection, ...]:
        text = format_loaded_skills(self.skills)
        if not text:
            return ()
        return (
            ContextSection(
                key="session_skills",
                tag="loaded_skills",
                content=text,
                order=SectionOrder.SESSION_SKILLS,
                views=ALL_VIEWS,
            ),
        )
