"""tests/matmaster/tools/builtin/test_skill_tool.py"""

import asyncio
from unittest.mock import MagicMock

from matmaster.tools.builtin.skill_tool import SkillTool


def make_skill(body="# Test Skill\nDo things.", mcp=None, deps=None):
    skill = MagicMock()
    skill.get_full_info.return_value = body
    skill.skill_path.resolve.return_value = "/skills/test-skill"
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

    def test_schema_has_args_param(self):
        tool = SkillTool(skill_registry=make_registry())
        assert "args" in tool.json_schema["properties"]


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

    def test_mcp_hit_callback(self):
        skill = make_skill(mcp="my-server")
        callback = MagicMock()
        tool = SkillTool(
            skill_registry=make_registry(skill=skill),
            on_skill_hit=callback,
        )
        asyncio.run(tool.execute({"skill": "test-skill"}))
        callback.assert_called_with("my-server")

    def test_args_appended(self):
        skill = make_skill()
        tool = SkillTool(skill_registry=make_registry(skill=skill))
        result = asyncio.run(
            tool.execute({"skill": "test-skill", "args": "some args"})
        )
        assert "some args" in result

    def test_slash_prefix_stripped(self):
        skill = make_skill()
        tool = SkillTool(skill_registry=make_registry(skill=skill))
        result = asyncio.run(tool.execute({"skill": "/test-skill"}))
        assert "Test Skill" in result

    def test_no_registry_error(self):
        tool = SkillTool(skill_registry=None)
        result = asyncio.run(tool.execute({"skill": "test-skill"}))
        assert "error" in result.lower()
