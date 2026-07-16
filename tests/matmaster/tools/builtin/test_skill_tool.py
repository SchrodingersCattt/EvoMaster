"""tests/matmaster/tools/builtin/test_skill_tool.py"""

import asyncio
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
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

    def test_planned_root_map_renders_node_side_paths_while_cold(self):
        """冷态 deferred session：本地 plugin 技能路径按规划映射渲染成节点侧路径。"""
        session = SimpleNamespace(
            planned_skill_root_map=(
                ("/app/matmaster/plugins", "/personal/.matmaster/plugins"),
                ("/app/matmaster/skills", "/personal/.matmaster/skills"),
            ),
            remote_project_root=None,
        )
        skill = make_skill(
            body="Scripts: ${SKILL_DIR}/scripts\nPlugin root: ${PLUGIN_DIR}"
        )
        skill.is_remote = False
        skill.skill_path = Path("/app/matmaster/plugins/mlips/skills/mlips")
        skill.plugin_dir = Path("/app/matmaster/plugins/mlips")
        tool = SkillTool(session=session, skill_registry=make_registry(skill=skill))

        result = asyncio.run(tool.execute({"skill": "mlips"}))

        expected = "/personal/.matmaster/plugins/mlips/skills/mlips"
        assert f"Base directory for this skill: {expected}" in result
        assert f"Scripts: {expected}/scripts" in result
        assert "Plugin root: /personal/.matmaster/plugins/mlips" in result
        assert "/app/matmaster" not in result

    def test_planned_root_map_wins_over_remote_project_root(self):
        from matmaster.tools.builtin.skill_tool import _PROJECT_ROOT

        local_root = _PROJECT_ROOT / "matmaster" / "plugins"
        session = SimpleNamespace(
            planned_skill_root_map=((str(local_root), "/personal/.matmaster/plugins"),),
            remote_project_root="/share/.matmaster",
        )
        skill = make_skill()
        skill.is_remote = False
        skill.skill_path = local_root / "mlips" / "skills" / "mlips"
        skill.plugin_dir = None
        tool = SkillTool(session=session, skill_registry=make_registry(skill=skill))

        result = asyncio.run(tool.execute({"skill": "mlips"}))

        assert (
            "Base directory for this skill: "
            "/personal/.matmaster/plugins/mlips/skills/mlips" in result
        )
        assert "/share/.matmaster" not in result

    def test_path_outside_planned_roots_falls_back_to_local(self):
        session = SimpleNamespace(
            planned_skill_root_map=(
                ("/app/matmaster/plugins", "/personal/.matmaster/plugins"),
            ),
            remote_project_root=None,
        )
        skill = make_skill()
        skill.is_remote = False
        skill.skill_path = Path("/elsewhere/custom-skill")
        skill.plugin_dir = None
        tool = SkillTool(session=session, skill_registry=make_registry(skill=skill))

        result = asyncio.run(tool.execute({"skill": "custom-skill"}))

        assert "Base directory for this skill: /elsewhere/custom-skill" in result

    def test_remote_plugin_dir_not_remapped_through_project_root(self):
        """远端 skill 的 plugin_dir 原样直出，不走 remote_project_root 本地映射。"""
        from matmaster.tools.builtin.skill_tool import _PROJECT_ROOT

        remote_plugin_dir = PurePosixPath(_PROJECT_ROOT.as_posix()) / "plugins/pack"
        session = MagicMock()
        session.remote_project_root = "/remote/proj"
        skill = make_skill(body="Plugin root: ${PLUGIN_DIR}")
        skill.is_remote = True
        skill.skill_path = remote_plugin_dir / "skills/member"
        skill.plugin_dir = remote_plugin_dir
        tool = SkillTool(session=session, skill_registry=make_registry(skill=skill))
        result = asyncio.run(tool.execute({"skill": "member"}))
        assert f"Plugin root: {remote_plugin_dir}" in result
        assert "/remote/proj" not in result.split("Plugin root: ")[1]


class TestRegistryProviderRefresh:
    def test_provider_result_replaces_registry_between_calls(self):
        local_skill = make_skill(body="local body")
        remote_skill = make_skill(body="remote body")
        provider = MagicMock(
            side_effect=[
                make_registry(skill=local_skill),
                make_registry(skill=remote_skill),
            ]
        )
        tool = SkillTool(skill_registry=None, registry_provider=provider)

        first = asyncio.run(tool.execute({"skill": "s"}))
        second = asyncio.run(tool.execute({"skill": "s"}))

        assert "local body" in first
        assert "remote body" in second

    def test_provider_returning_none_keeps_constructor_registry(self):
        skill = make_skill(body="constructor body")
        tool = SkillTool(
            skill_registry=make_registry(skill=skill),
            registry_provider=MagicMock(return_value=None),
        )
        result = asyncio.run(tool.execute({"skill": "s"}))
        assert "constructor body" in result

    def test_provider_error_keeps_constructor_registry(self):
        skill = make_skill(body="constructor body")
        tool = SkillTool(
            skill_registry=make_registry(skill=skill),
            registry_provider=MagicMock(side_effect=RuntimeError("boom")),
        )
        result = asyncio.run(tool.execute({"skill": "s"}))
        assert "constructor body" in result
