"""Tests for matmaster.tools.skill_tool — SkillTool implementing Tool Protocol."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


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


def _make_script(skill_dir: Path, script_name: str, content: str = "print('ok')") -> Path:
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    script_path = scripts_dir / script_name
    script_path.write_text(content, encoding="utf-8")
    return script_path


def _make_reference(skill_dir: Path, ref_name: str, content: str = "ref content") -> Path:
    ref_dir = skill_dir / "references"
    ref_dir.mkdir(parents=True, exist_ok=True)
    ref_path = ref_dir / ref_name
    ref_path.write_text(content, encoding="utf-8")
    return ref_path


def _mock_session() -> MagicMock:
    session = MagicMock()
    session.remote_project_root = None
    session.exec_bash.return_value = {"stdout": "output", "stderr": "", "exit_code": 0}
    return session


# ---------------------------------------------------------------------------
# get_info tests
# ---------------------------------------------------------------------------


class TestGetInfo:
    """Tests for the get_info action."""

    async def test_triggers_on_skill_hit_for_mcp_server(self, tmp_path: Path) -> None:
        """get_info calls on_skill_hit with the mcp_server value."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        _make_skill(tmp_path, "calc", mcp_server="calc-server")
        registry = SkillRegistry(tmp_path)
        callback = MagicMock()
        tool = SkillTool(registry, _mock_session(), on_skill_hit=callback)

        result = await tool.execute({"skill_name": "calc", "action": "get_info"})
        callback.assert_called_once_with("calc-server")
        assert "# Skill: calc" in result
        assert "Body" in result

    async def test_depends_on_cascades_to_multiple_servers(self, tmp_path: Path) -> None:
        """get_info with depends_on triggers on_skill_hit for each dependency's mcp_server."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        _make_skill(tmp_path, "workflow", depends_on="tool-a,tool-b")
        _make_skill(tmp_path, "tool-a", mcp_server="server-a")
        _make_skill(tmp_path, "tool-b", mcp_server="server-b")
        registry = SkillRegistry(tmp_path)
        callback = MagicMock()
        tool = SkillTool(registry, _mock_session(), on_skill_hit=callback)

        await tool.execute({"skill_name": "workflow", "action": "get_info"})
        calls = [c.args[0] for c in callback.call_args_list]
        assert "server-a" in calls
        assert "server-b" in calls

    async def test_skill_not_found_returns_error(self, tmp_path: Path) -> None:
        """get_info for nonexistent skill returns error string."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        registry = SkillRegistry(tmp_path)
        tool = SkillTool(registry, _mock_session())

        result = await tool.execute({"skill_name": "nope", "action": "get_info"})
        assert "Error" in result
        assert "nope" in result


# ---------------------------------------------------------------------------
# get_reference tests
# ---------------------------------------------------------------------------


class TestGetReference:
    """Tests for the get_reference action."""

    async def test_returns_reference_content(self, tmp_path: Path) -> None:
        """get_reference returns reference file content."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        skill_dir = _make_skill(tmp_path, "calc")
        _make_reference(skill_dir, "api.md", "API docs here")
        registry = SkillRegistry(tmp_path)
        tool = SkillTool(registry, _mock_session())

        result = await tool.execute({
            "skill_name": "calc",
            "action": "get_reference",
            "reference_name": "api.md",
        })
        assert "API docs here" in result

    async def test_missing_reference_falls_back_to_full_info(self, tmp_path: Path) -> None:
        """get_reference falls back to full skill info when reference not found."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        _make_skill(tmp_path, "calc", body="Full calc info here")
        registry = SkillRegistry(tmp_path)
        tool = SkillTool(registry, _mock_session())

        result = await tool.execute({
            "skill_name": "calc",
            "action": "get_reference",
            "reference_name": "nonexistent.md",
        })
        assert "Fallback to skill info" in result
        assert "Full calc info here" in result

    async def test_co_template_hint_appended(self, tmp_path: Path) -> None:
        """get_reference appends co-template hint when _co_templates.json exists."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        skill_dir = _make_skill(tmp_path, "calc")
        _make_reference(skill_dir, "base.md", "Base template content")

        co_templates = {
            "base.md": {
                "hint": "This template needs a header too.",
                "related": ["header.md", "footer.md"],
            }
        }
        co_path = skill_dir / "references" / "_co_templates.json"
        co_path.write_text(json.dumps(co_templates), encoding="utf-8")

        registry = SkillRegistry(tmp_path)
        tool = SkillTool(registry, _mock_session())

        result = await tool.execute({
            "skill_name": "calc",
            "action": "get_reference",
            "reference_name": "base.md",
        })
        assert "CO-TEMPLATE REMINDER" in result
        assert "header.md" in result
        assert "footer.md" in result
        assert "=" * 72 in result


# ---------------------------------------------------------------------------
# run_script tests
# ---------------------------------------------------------------------------


class TestRunScript:
    """Tests for the run_script action."""

    async def test_executes_script_via_session(self, tmp_path: Path) -> None:
        """run_script calls session.exec_bash with the constructed command."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        skill_dir = _make_skill(tmp_path, "calc")
        _make_script(skill_dir, "run.py")
        registry = SkillRegistry(tmp_path)
        session = _mock_session()
        tool = SkillTool(registry, session)

        result = await tool.execute({
            "skill_name": "calc",
            "action": "run_script",
            "script_name": "run.py",
        })
        session.exec_bash.assert_called_once()
        cmd = session.exec_bash.call_args[0][0]
        assert "run.py" in cmd
        assert "Script output:" in result

    async def test_auto_infers_single_script_name(self, tmp_path: Path) -> None:
        """run_script infers script_name when skill has exactly one script."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        skill_dir = _make_skill(tmp_path, "single")
        _make_script(skill_dir, "only.py")
        registry = SkillRegistry(tmp_path)
        session = _mock_session()
        tool = SkillTool(registry, session)

        result = await tool.execute({
            "skill_name": "single",
            "action": "run_script",
        })
        session.exec_bash.assert_called_once()
        cmd = session.exec_bash.call_args[0][0]
        assert "only.py" in cmd
        assert "Script output:" in result

    async def test_prompts_auto_inject(self, tmp_path: Path) -> None:
        """run_script auto-injects prompt content when prompts/<token>.md exists."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        skill_dir = _make_skill(tmp_path, "survey")
        _make_script(skill_dir, "run.py")

        # Create prompts directory with a mode-specific prompt
        prompts_dir = skill_dir / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        (prompts_dir / "deep.md").write_text("Deep mode workflow instructions", encoding="utf-8")

        registry = SkillRegistry(tmp_path)
        session = _mock_session()
        tool = SkillTool(registry, session)

        result = await tool.execute({
            "skill_name": "survey",
            "action": "run_script",
            "script_name": "run.py",
            "script_args": "deep --verbose",
        })
        assert "MANDATORY WORKFLOW" in result
        assert "Deep mode workflow instructions" in result

    async def test_injects_credentials_before_exec(self, tmp_path: Path) -> None:
        """run_script injects session credentials into the command."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        skill_dir = _make_skill(tmp_path, "calc")
        _make_script(skill_dir, "run.py")
        registry = SkillRegistry(tmp_path)
        session = _mock_session()
        session._bohrium_credentials = {
            "access_key": "test_ak",
            "project_id": 123,
        }
        tool = SkillTool(registry, session)

        await tool.execute({
            "skill_name": "calc",
            "action": "run_script",
            "script_name": "run.py",
        })

        # write_file should have been called with credential content
        session.write_file.assert_called_once()
        content = session.write_file.call_args[0][1]
        assert "BOHRIUM_ACCESS_KEY" in content

        # exec_bash called twice: chmod + actual command
        assert session.exec_bash.call_count == 2
        chmod_call = session.exec_bash.call_args_list[0][0][0]
        assert "chmod 600" in chmod_call
        run_call = session.exec_bash.call_args_list[1][0][0]
        assert "run.py" in run_call
        assert ". /tmp/.mm_env_" in run_call


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Tests for error handling in SkillTool."""

    async def test_exec_bash_exception_caught(self, tmp_path: Path) -> None:
        """Exception in session.exec_bash is caught and returned as Error string."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        skill_dir = _make_skill(tmp_path, "calc")
        _make_script(skill_dir, "run.py")
        registry = SkillRegistry(tmp_path)
        session = _mock_session()
        session.exec_bash.side_effect = RuntimeError("connection lost")
        tool = SkillTool(registry, session)

        result = await tool.execute({
            "skill_name": "calc",
            "action": "run_script",
            "script_name": "run.py",
        })
        assert "Error" in result
        assert "connection lost" in result


# ---------------------------------------------------------------------------
# depends_on with multi-root
# ---------------------------------------------------------------------------


class TestDependsOnMultiRoot:
    """Tests for depends_on cascading across skill roots."""

    async def test_workflow_skill_cascades_to_lazymcp_skills(self, tmp_path: Path) -> None:
        """Workflow skill with depends_on cascades to skills in different roots."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        root1 = tmp_path / "core_skills"
        root2 = tmp_path / "mcp_skills"

        # Workflow skill in root1 depends on tools in root2
        _make_skill(root1, "workflow", depends_on="mcp-tool-x,mcp-tool-y")
        _make_skill(root2, "mcp-tool-x", mcp_server="server-x")
        _make_skill(root2, "mcp-tool-y", mcp_server="server-y")

        registry = SkillRegistry([root1, root2])
        callback = MagicMock()
        tool = SkillTool(registry, _mock_session(), on_skill_hit=callback)

        await tool.execute({"skill_name": "workflow", "action": "get_info"})
        calls = [c.args[0] for c in callback.call_args_list]
        assert "server-x" in calls
        assert "server-y" in calls


# ---------------------------------------------------------------------------
# Tool Protocol conformance
# ---------------------------------------------------------------------------


class TestToolProtocol:
    """Tests that SkillTool conforms to the matmaster Tool Protocol."""

    def test_satisfies_tool_protocol(self, tmp_path: Path) -> None:
        """SkillTool is an instance of the Tool protocol."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool
        from matmaster.tools.tool_registry import Tool

        registry = SkillRegistry(tmp_path)
        tool = SkillTool(registry, _mock_session())
        assert isinstance(tool, Tool)

    def test_name_property(self, tmp_path: Path) -> None:
        """name property returns 'use_skill'."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        registry = SkillRegistry(tmp_path)
        tool = SkillTool(registry, _mock_session())
        assert tool.name == "use_skill"

    def test_description_property(self, tmp_path: Path) -> None:
        """description property mentions the 3 actions."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        registry = SkillRegistry(tmp_path)
        tool = SkillTool(registry, _mock_session())
        desc = tool.description
        assert "get_info" in desc
        assert "get_reference" in desc
        assert "run_script" in desc

    def test_json_schema_structure(self, tmp_path: Path) -> None:
        """json_schema has required skill_name and action with correct enum."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        registry = SkillRegistry(tmp_path)
        tool = SkillTool(registry, _mock_session())
        schema = tool.json_schema
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "skill_name" in props
        assert "action" in props
        assert set(props["action"]["enum"]) == {"get_info", "get_reference", "run_script"}
        assert set(schema["required"]) == {"skill_name", "action"}
