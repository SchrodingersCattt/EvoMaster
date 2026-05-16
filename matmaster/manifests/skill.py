"""Phase 2B shim delegating to matmaster.context.sources.skills."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from matmaster.context.sources.skills import (
    format_loaded_skills as _typed_format,
    skill_name as _typed_skill_name,
)
from matmaster.manifests.scanner import scan_skill_hits

__all__ = ["skill_name", "resolve_active_skills", "format_loaded_skills"]


def skill_name(skill: Any) -> str:
    return _typed_skill_name(skill)


def resolve_active_skills(
    events: Iterable[dict[str, Any]],
    skill_registry: Any,
) -> list[Any]:
    skills: list[Any] = []
    if skill_registry is None:
        return skills
    for record in scan_skill_hits(events):
        try:
            skill = skill_registry.get_skill(record.skill_name)
        except Exception:
            continue
        if skill is not None:
            skills.append(skill)
    return skills


def format_loaded_skills(skills: Iterable[Any]) -> str:
    return _typed_format(skills)
