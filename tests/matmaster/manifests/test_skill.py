from pathlib import Path

from matmaster.manifests.skill import format_loaded_skills, resolve_active_skills
from matmaster.skills.registry import SkillRegistry


def _write_skill(root: Path, name: str, description: str, mcp_server: str = "") -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    mcp_line = f"mcp_server: {mcp_server}\n" if mcp_server else ""
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{mcp_line}---\nbody\n",
        encoding="utf-8",
    )


def test_resolve_active_skills_uses_skill_hit_events_only(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "pxrd", "PXRD helper", "mat_xrd")
    _write_skill(root, "mlip", "MLIP helper")
    registry = SkillRegistry([root])

    events = [
        {
            "type": "assistant_state",
            "content": {"tool_calls": [{"name": "mat_xrd_read"}]},
        },
        {"type": "tool_call", "tool_name": "mat_xrd_read"},
        {"id": 1, "type": "skill_hit", "content": {"skill_name": "pxrd"}},
        {"id": 2, "type": "skill_hit", "content": {"skill_name": "mlip"}},
        {"id": 3, "type": "skill_hit", "content": {"skill_name": "missing"}},
    ]

    skills = resolve_active_skills(events, registry)

    assert [skill.meta_info.name for skill in skills] == ["pxrd", "mlip"]


def test_format_loaded_skills_outputs_compact_block(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "pxrd", "PXRD helper", "mat_xrd")
    registry = SkillRegistry([root])

    text = format_loaded_skills(
        resolve_active_skills(
            [{"id": 1, "type": "skill_hit", "content": {"skill_name": "pxrd"}}],
            registry,
        )
    )

    assert "[Loaded skills]" in text
    assert "- pxrd: PXRD helper" in text
    assert "mcp_server=mat_xrd" in text
