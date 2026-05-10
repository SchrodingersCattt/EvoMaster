from __future__ import annotations

from typing import Any

from matmaster.manifests.scanner import scan_skill_hits


def _skill_name(skill: Any) -> str:
    return str(
        getattr(skill, "name", "")
        or getattr(getattr(skill, "meta_info", None), "name", "")
    ).strip()


def resolve_active_skills(events: list[dict[str, Any]], skill_registry: Any) -> list[Any]:
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


def format_loaded_skills(skills: list[Any]) -> str:
    if not skills:
        return ""
    lines = ["[Loaded skills]"]
    for skill in skills:
        name = _skill_name(skill)
        meta = getattr(skill, "meta_info", None)
        description = getattr(meta, "description", "") or ""
        mcp_server = getattr(meta, "mcp_server", None)
        suffix = f" (mcp_server={mcp_server})" if mcp_server else ""
        if description:
            lines.append(f"- {name}: {description}{suffix}")
        else:
            lines.append(f"- {name}{suffix}")
    return "\n".join(lines)

