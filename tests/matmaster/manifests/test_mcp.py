from pathlib import Path

from matmaster.manifests.mcp import (
    format_active_mcp,
    resolve_declared_servers,
    resolve_runnable_servers,
)
from matmaster.manifests.skill import resolve_active_skills
from matmaster.skills.registry import SkillRegistry


def _write_skill(root: Path, name: str, mcp_server: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} helper\nmcp_server: {mcp_server}\n---\nbody\n",
        encoding="utf-8",
    )


def _skills(root: Path):
    registry = SkillRegistry([root])
    events = [
        {"id": 1, "type": "skill_hit", "content": {"skill_name": "pxrd"}},
        {"id": 2, "type": "skill_hit", "content": {"skill_name": "sg"}},
    ]
    return resolve_active_skills(events, registry)


def test_resolve_declared_servers_uses_skill_metadata_only(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "pxrd", "mat_xrd")
    _write_skill(root, "sg", "mat_sg")

    assert resolve_declared_servers(_skills(root)) == {"mat_xrd", "mat_sg"}


def test_resolve_runnable_servers_filters_by_legal_servers(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "pxrd", "mat_xrd")
    _write_skill(root, "sg", "mat_sg")

    assert resolve_runnable_servers(
        _skills(root),
        legal_servers={"mat_sg"},
    ) == {"mat_sg"}


def test_format_active_mcp_marks_unavailable_servers(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "pxrd", "mat_xrd")

    text = format_active_mcp(
        _skills(root),
        legal_servers=set(),
        schemas_by_server={},
    )

    assert "[Active MCP servers]" in text
    assert "- mat_xrd: unavailable" in text
