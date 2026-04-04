"""Tests for builtin SkillTool — prompt expansion and skill dispatch."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from matmaster.tools.builtin.skill_tool import SkillTool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill(
    root: Path,
    name: str,
    *,
    mcp_server: str | None = None,
    depends_on: str | None = None,
    body: str = "Body",
) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    fm = f"---\nname: {name}\ndescription: test\n"
    if mcp_server:
        fm += f"mcp_server: {mcp_server}\n"
    if depends_on:
        fm += f"depends_on: {depends_on}\n"
    fm += f"---\n\n{body}\n"
    (d / "SKILL.md").write_text(fm, encoding="utf-8")
    return d


def _make_tool(tmp_path: Path, on_skill_hit=None) -> SkillTool:
    from matmaster.skills.registry import SkillRegistry

    registry = SkillRegistry(tmp_path)
    return SkillTool(skill_registry=registry, on_skill_hit=on_skill_hit)


# ---------------------------------------------------------------------------
# Prompt expansion
# ---------------------------------------------------------------------------


class TestPromptExpansion:
    async def test_returns_body_with_base_directory_header(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path, "alpha", body="Workflow instructions here.")
        tool = _make_tool(tmp_path)

        result = await tool.execute({"skill": "alpha"})
        expected_dir = str(skill_dir.resolve())
        assert result.startswith(f"Base directory for this skill: {expected_dir}\n\n")
        assert "Workflow instructions here." in result

    async def test_skill_dir_substitution(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(
            tmp_path,
            "beta",
            body="See files under ${SKILL_DIR}/data",
        )
        tool = _make_tool(tmp_path)

        result = await tool.execute({"skill": "beta"})
        resolved = str(skill_dir.resolve())
        assert "${SKILL_DIR}" not in result
        assert f"{resolved}/data" in result

    async def test_expansion_is_frontmatter_stripped(self, tmp_path: Path) -> None:
        """Expansion must be SKILL.md body only, not YAML frontmatter (Chunk 2 contract)."""
        body_only = "EXPANSION_BODY_NO_FRONTMATTER"
        _make_skill(tmp_path, "calc", body=body_only)
        tool = _make_tool(tmp_path)

        result = await tool.execute({"skill": "calc"})
        assert body_only in result
        assert "name: calc" not in result
        assert "description: test" not in result
        assert "---" not in result

    async def test_nonexistent_skill_returns_error_string(self, tmp_path: Path) -> None:
        tool = _make_tool(tmp_path)

        result = await tool.execute({"skill": "nope"})
        assert result == "Error: Skill 'nope' not found"

    async def test_slash_prefix_stripped(self, tmp_path: Path) -> None:
        _make_skill(tmp_path, "alpha", body="Workflow instructions here.")
        tool = _make_tool(tmp_path)

        result = await tool.execute({"skill": "/alpha"})
        assert "Workflow instructions here." in result


# ---------------------------------------------------------------------------
# on_skill_hit
# ---------------------------------------------------------------------------


class TestOnSkillHit:
    async def test_callback_invoked_for_mcp_server(self, tmp_path: Path) -> None:
        _make_skill(tmp_path, "calc", mcp_server="calc-server")
        callback = MagicMock()
        tool = _make_tool(tmp_path, on_skill_hit=callback)

        await tool.execute({"skill": "calc"})
        callback.assert_called_once_with("calc-server")

    async def test_no_callback_when_no_mcp_server(self, tmp_path: Path) -> None:
        _make_skill(tmp_path, "plain")
        callback = MagicMock()
        tool = _make_tool(tmp_path, on_skill_hit=callback)

        await tool.execute({"skill": "plain"})
        callback.assert_not_called()

    async def test_depends_on_cascades_to_multiple_mcp_servers(
        self, tmp_path: Path
    ) -> None:
        _make_skill(tmp_path, "workflow", depends_on="tool-a,tool-b")
        _make_skill(tmp_path, "tool-a", mcp_server="server-a")
        _make_skill(tmp_path, "tool-b", mcp_server="server-b")
        callback = MagicMock()
        tool = _make_tool(tmp_path, on_skill_hit=callback)

        await tool.execute({"skill": "workflow"})
        calls = [c.args[0] for c in callback.call_args_list]
        assert "server-a" in calls
        assert "server-b" in calls

    async def test_no_callback_registered_is_fine(self, tmp_path: Path) -> None:
        _make_skill(tmp_path, "solo", mcp_server="srv", body="Doc body")
        tool = _make_tool(tmp_path)

        result = await tool.execute({"skill": "solo"})
        assert "Doc body" in result
        assert "Base directory for this skill:" in result

    async def test_missing_dependency_does_not_crash(self, tmp_path: Path) -> None:
        _make_skill(tmp_path, "wf", depends_on="ghost-dep", body="Still ok")
        tool = _make_tool(tmp_path, on_skill_hit=MagicMock())

        result = await tool.execute({"skill": "wf"})
        assert "Still ok" in result
        assert "Error:" not in result

    async def test_depends_on_multi_root_still_cascades(self, tmp_path: Path) -> None:
        from matmaster.skills.registry import SkillRegistry

        root1 = tmp_path / "core_skills"
        root2 = tmp_path / "mcp_skills"
        _make_skill(root1, "workflow", depends_on="mcp-tool-x,mcp-tool-y")
        _make_skill(root2, "mcp-tool-x", mcp_server="server-x")
        _make_skill(root2, "mcp-tool-y", mcp_server="server-y")

        registry = SkillRegistry([root1, root2])
        callback = MagicMock()
        tool = SkillTool(skill_registry=registry, on_skill_hit=callback)

        await tool.execute({"skill": "workflow"})
        calls = [c.args[0] for c in callback.call_args_list]
        assert "server-x" in calls
        assert "server-y" in calls


# ---------------------------------------------------------------------------
# Tool Protocol
# ---------------------------------------------------------------------------


class TestToolProtocol:
    def test_name_property(self, tmp_path: Path) -> None:
        tool = _make_tool(tmp_path)
        assert tool.name == "Skill"

    def test_json_schema_skill_param(self, tmp_path: Path) -> None:
        tool = _make_tool(tmp_path)
        schema = tool.json_schema
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "skill" in props
        assert "args" in props
        assert schema["required"] == ["skill"]

    def test_description_explains_activation(self, tmp_path: Path) -> None:
        tool = _make_tool(tmp_path)
        desc = tool.description.lower()
        assert "skill" in desc

    def test_metadata_defaults_include_control_plane(self, tmp_path: Path) -> None:
        from matmaster.types.topology import ToolPlane

        tool = _make_tool(tmp_path)

        assert tool.capabilities == frozenset({"skill.dispatch"})
        assert tool.effect_level == "local_mutation"
        assert tool.plane == ToolPlane.CONTROL_PLANE
