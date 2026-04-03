from __future__ import annotations

import asyncio
from matmaster.skills.registry import SkillRegistry
from matmaster.tools.skill_tool import SkillTool


class TestSkillToolCallback:
    def _make_skill_dir(self, tmp_path, name, mcp_server=None):
        skill_dir = tmp_path / name
        skill_dir.mkdir()
        lines = [f"---\nname: {name}\ndescription: Test skill\n"]
        if mcp_server:
            lines.append(f"mcp_server: {mcp_server}\n")
        lines.append("---\nSkill body\n")
        (skill_dir / "SKILL.md").write_text("".join(lines))
        return skill_dir

    def test_callback_invoked_with_mcp_server(self, tmp_path):
        self._make_skill_dir(tmp_path, "test-skill", mcp_server="mat_sg")
        registry = SkillRegistry(tmp_path)
        hit_servers = []
        tool = SkillTool(registry, on_skill_hit=lambda s: hit_servers.append(s))

        asyncio.run(tool.execute({"skill_name": "test-skill"}))
        assert hit_servers == ["mat_sg"]

    def test_callback_not_invoked_without_mcp_server(self, tmp_path):
        self._make_skill_dir(tmp_path, "plain-skill")
        registry = SkillRegistry(tmp_path)
        hit_servers = []
        tool = SkillTool(registry, on_skill_hit=lambda s: hit_servers.append(s))

        asyncio.run(tool.execute({"skill_name": "plain-skill"}))
        assert hit_servers == []

    def test_no_callback_is_fine(self, tmp_path):
        self._make_skill_dir(tmp_path, "test-skill", mcp_server="mat_sg")
        registry = SkillRegistry(tmp_path)
        tool = SkillTool(registry)  # No callback

        result = asyncio.run(tool.execute({"skill_name": "test-skill"}))
        assert "Skill body" in result  # Still returns expanded skill body
