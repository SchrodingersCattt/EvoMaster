from pathlib import Path

from matmaster.context.skill_resolver import SkillRegistryResolver
from matmaster.context.sources.tools import resolve_runnable_servers
from src.services.session_event_codec import decode_session_events
from src.services.skill_registry_factory import build_skill_registry


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
    registry = build_skill_registry(config_roots=(root,), session=None)
    events = [{"id": 1, "type": "skill_hit", "content": {"skill_name": "test-skill"}}]
    resolver = SkillRegistryResolver(registry)

    skills = resolver(decode_session_events(events))
    servers = resolve_runnable_servers(skills, legal_servers={"mat_sg"})

    assert servers == {"mat_sg"}


def test_value_wrapped_skill_hit_resolves_runnable_server(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "test-skill", "mat_sg")
    registry = build_skill_registry(config_roots=(root,), session=None)
    events = [{"id": 1, "type": "skill_hit", "content": "test-skill"}]
    resolver = SkillRegistryResolver(registry)

    skills = resolver(decode_session_events(events))

    assert [skill.name for skill in skills] == ["test-skill"]
    assert skills[0].mcp_server == "mat_sg"


def test_tool_call_event_without_skill_hit_does_not_activate_mcp(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "test-skill", "mat_sg")
    registry = build_skill_registry(config_roots=(root,), session=None)
    events = [{"id": 1, "type": "tool_call", "tool_name": "mat_sg_build_bulk"}]
    resolver = SkillRegistryResolver(registry)

    skills = resolver(decode_session_events(events))
    servers = resolve_runnable_servers(skills, legal_servers={"mat_sg"})

    assert servers == set()
