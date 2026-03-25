"""Validate all lazymcp SKILL.md files parse correctly."""
from __future__ import annotations

from pathlib import Path

import pytest

LAZYMCP_ROOT = Path(__file__).resolve().parent.parent / "matmaster" / "skills" / "lazymcp"

EXPECTED_SKILLS = {
    "mcp-mat-sg": "mat_sg",
    "mcp-mat-sn": "mat_sn",
    "mcp-mat-doc": "mat_doc",
    "mcp-mat-dpa": "mat_dpa",
    "mcp-mat-compdart": "mat_compdart",
    "mcp-mat-struct-db": "mat_struct_db",
    "mcp-mat-nmr": "mat_nmr",
    "mcp-mat-xrd": "mat_xrd",
    "mcp-mat-electron-microscope": "mat_electron_microscope",
}


@pytest.mark.parametrize("skill_name,expected_server", EXPECTED_SKILLS.items())
def test_skill_parses_and_has_mcp_server(skill_name: str, expected_server: str):
    from matmaster.skills.registry import Skill

    skill_dir = LAZYMCP_ROOT / skill_name
    assert skill_dir.exists(), f"Directory missing: {skill_dir}"

    skill = Skill(skill_dir)
    assert skill.meta_info.name == skill_name
    assert skill.meta_info.extras.get("mcp_server") == expected_server
    assert skill.meta_info.description
    assert skill.get_full_info()


def test_all_expected_skills_exist():
    for name in EXPECTED_SKILLS:
        assert (LAZYMCP_ROOT / name / "SKILL.md").exists(), f"Missing: {name}/SKILL.md"


def test_registry_loads_all_lazymcp():
    from matmaster.skills.registry import SkillRegistry

    reg = SkillRegistry(LAZYMCP_ROOT)
    for name in EXPECTED_SKILLS:
        assert reg.get_skill(name) is not None, f"Registry missing: {name}"
