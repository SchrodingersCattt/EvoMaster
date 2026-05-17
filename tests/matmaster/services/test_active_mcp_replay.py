from pathlib import Path

from matmaster.context.sources.skills import resolve_active_skills
from matmaster.context.sources.tools import resolve_runnable_servers
from matmaster.skills.registry import SkillRegistry
from src.services.session_event_codec import decode_session_events


def _write_skill(root: Path, name: str, mcp_server: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: T\nmcp_server: {mcp_server}\n---\nbody\n",
        encoding="utf-8",
    )


def test_skill_hit_resolves_runnable_server(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "test-skill", "mat_sg")
    registry = SkillRegistry([root])
    events = [{"id": 1, "type": "skill_hit", "content": {"skill_name": "test-skill"}}]

    skills = resolve_active_skills(decode_session_events(events), registry)
    servers = resolve_runnable_servers(skills, legal_servers={"mat_sg"})

    assert servers == {"mat_sg"}


def test_value_wrapped_skill_hit_resolves_runnable_server(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "test-skill", "mat_sg")
    registry = SkillRegistry([root])
    events = [{"id": 1, "type": "skill_hit", "content": "test-skill"}]

    skills = resolve_active_skills(decode_session_events(events), registry)

    assert [skill.meta_info.name for skill in skills] == ["test-skill"]


def test_tool_call_event_without_skill_hit_does_not_activate_mcp(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "test-skill", "mat_sg")
    registry = SkillRegistry([root])
    events = [{"id": 1, "type": "tool_call", "tool_name": "mat_sg_build_bulk"}]

    skills = resolve_active_skills(decode_session_events(events), registry)
    servers = resolve_runnable_servers(skills, legal_servers={"mat_sg"})

    assert servers == set()
