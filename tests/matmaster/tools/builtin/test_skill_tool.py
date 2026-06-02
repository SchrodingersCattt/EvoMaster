"""tests/matmaster/tools/builtin/test_skill_tool.py"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from matmaster.tools.builtin.skill_tool import SkillTool


def make_skill(body="# Test Skill\nDo things.", mcp=None, deps=None):
    skill = MagicMock()
    skill.get_full_info.return_value = body
    skill.skill_path = Path("/skills/test-skill")
    skill.meta_info.mcp_server = mcp
    skill.meta_info.depends_on = deps or []
    return skill


def make_registry(skill=None):
    registry = MagicMock()
    registry.get_skill.return_value = skill
    return registry


class TestSkillToolMetadata:
    def test_name(self):
        assert SkillTool.name == "Skill"

    def test_schema_has_skill_param(self):
        tool = SkillTool(skill_registry=make_registry())
        assert "skill" in tool.json_schema["properties"]

    def test_schema_does_not_have_args_param(self):
        tool = SkillTool(skill_registry=make_registry())
        assert "args" not in tool.json_schema["properties"]

    def test_schema_does_not_have_legacy_skill_name_param(self):
        tool = SkillTool(skill_registry=make_registry())
        assert "skill_name" not in tool.json_schema["properties"]


class TestSkillToolDescriptionSlim:
    def test_description_is_short_summary(self) -> None:
        assert SkillTool.description.startswith("Activate a skill by name")
        assert len(SkillTool.description) < 200

    def test_prompt_remains_detailed(self) -> None:
        tool = SkillTool(skill_registry=make_registry())
        prompt = tool.prompt()
        assert prompt is not None
        assert "How to invoke" in prompt or "slash command" in prompt.lower()


class TestSkillExecution:
    def test_skill_not_found(self):
        tool = SkillTool(skill_registry=make_registry(skill=None))
        result = asyncio.run(tool.execute({"skill": "nonexistent"}))
        assert "error" in result.lower()

    def test_skill_found(self):
        skill = make_skill()
        tool = SkillTool(skill_registry=make_registry(skill=skill))
        result = asyncio.run(tool.execute({"skill": "test-skill"}))
        assert "Test Skill" in result
        assert "/skills/test-skill" in result

    def test_legacy_skill_name_param_not_accepted(self):
        skill = make_skill()
        registry = make_registry(skill=skill)
        registry.get_skill.side_effect = lambda name: (
            skill if name == "test-skill" else None
        )
        tool = SkillTool(skill_registry=registry)
        result = asyncio.run(tool.execute({"skill_name": "test-skill"}))
        assert "error" in result.lower()
        registry.get_skill.assert_called_with("")

    def test_mcp_hit_callback(self):
        skill = make_skill(mcp="my-server")
        callback = MagicMock()
        tool = SkillTool(
            skill_registry=make_registry(skill=skill),
            on_skill_hit=callback,
        )
        asyncio.run(tool.execute({"skill": "test-skill"}))
        callback.assert_called_with("my-server")

    def test_extra_args_ignored(self):
        skill = make_skill()
        tool = SkillTool(skill_registry=make_registry(skill=skill))
        result = asyncio.run(tool.execute({"skill": "test-skill", "args": "some args"}))
        assert "some args" not in result

    def test_slash_prefix_stripped(self):
        skill = make_skill()
        tool = SkillTool(skill_registry=make_registry(skill=skill))
        result = asyncio.run(tool.execute({"skill": "/test-skill"}))
        assert "Test Skill" in result

    def test_no_registry_error(self):
        tool = SkillTool(skill_registry=None)
        result = asyncio.run(tool.execute({"skill": "test-skill"}))
        assert "error" in result.lower()
