"""Tests for Exp._init_skill_tools with lazy MCP schema injection."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from matmaster.config.exp import ExpConfig
from matmaster.core.exp import Exp
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.core.playground import PlaygroundContext


def _make_skill_dir(tmp_path: Path) -> Path:
    skill_dir = tmp_path / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: Test\nmcp_server: mat_sg\n---\nBody\n"
    )
    return tmp_path / "skills"


def _make_cache(tmp_path: Path) -> Path:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    schemas = [{"name": "build_bulk", "description": "Build", "input_schema": {}}]
    (cache_dir / "mat_sg.json").write_text(json.dumps(schemas))
    return cache_dir


def _make_mcp_yaml(tmp_path: Path) -> None:
    import yaml

    (tmp_path / "mcp.yaml").write_text(
        yaml.dump(
            {
                "calculation_preflight": "calculation",
                "calculation_servers": ["mat_sg"],
            }
        )
    )


class FakeRemoteSkillSession:
    def __init__(self, root: str, files: dict[str, str]) -> None:
        self.remote_user_skills_root = root
        self.remote_project_root = None
        self.local_user_skills_root = None
        self._files = files

    def path_exists(self, path: str) -> bool:
        prefix = path.rstrip("/") + "/"
        return any(
            candidate == path or candidate.startswith(prefix)
            for candidate in self._files
        )

    def exec_bash(self, command: str, timeout: int | None = None) -> dict[str, object]:
        payload = [
            {"path": path, "content": self._files[path]}
            for path in sorted(self._files)
            if path.endswith("/SKILL.md")
        ]
        return {"exit_code": 0, "stdout": json.dumps(payload)}

    def read_file(self, path: str, encoding: str = "utf-8") -> str:
        return self._files[path]


class TestExpInitSkillTools:
    def test_skill_tools_registered_when_enabled(self, tmp_path):
        skills_root = _make_skill_dir(tmp_path)
        cache_dir = _make_cache(tmp_path)
        _make_mcp_yaml(tmp_path)

        cfg = ExpConfig.model_validate(
            {
                "name": "test",
                "skills": {
                    "enabled": True,
                    "skills_root": str(skills_root),
                    "cache_dir": str(cache_dir),
                    "config_dir": str(tmp_path),
                    "mcp_config_file": "mcp_config.json",
                    "mcp_runtime_file": "mcp.yaml",
                },
            }
        )
        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock(spec=PlaygroundContext)
        ctx.session = MagicMock()
        ctx.execution_workdir = str(tmp_path)

        exp._init_skill_tools(ctx, registry)

        assert "Skill" in registry

        from matmaster.tools.builtin.skill_tool import SkillTool as BuiltinSkillTool

        skill_tool = registry._tools["Skill"]
        assert isinstance(skill_tool, BuiltinSkillTool)

    def test_skill_tools_skipped_when_disabled(self, tmp_path):
        cfg = ExpConfig.model_validate(
            {
                "name": "test",
                "skills": {"enabled": False},
            }
        )
        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock(spec=PlaygroundContext)

        exp._init_skill_tools(ctx, registry)

        assert "Skill" not in registry

    async def test_skill_trigger_injects_lazy_tools(self, tmp_path):
        """Simulating skill hit triggers lazy MCP tool injection."""
        skills_root = _make_skill_dir(tmp_path)
        cache_dir = _make_cache(tmp_path)
        _make_mcp_yaml(tmp_path)
        (tmp_path / "mcp_config.json").write_text('{"mcpServers": {}}')

        cfg = ExpConfig.model_validate(
            {
                "name": "test",
                "skills": {
                    "enabled": True,
                    "skills_root": str(skills_root),
                    "cache_dir": str(cache_dir),
                    "config_dir": str(tmp_path),
                    "mcp_config_file": "mcp_config.json",
                    "mcp_runtime_file": "mcp.yaml",
                },
            }
        )
        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock(spec=PlaygroundContext)
        ctx.session = MagicMock()
        ctx.execution_workdir = str(tmp_path)

        exp._init_skill_tools(ctx, registry)

        # Before skill trigger: no MCP tools
        assert "mat_sg_build_bulk" not in registry

        assert "Skill" in registry
        skill_tool = registry._tools["Skill"]

        from matmaster.tools.tool_result import normalize_tool_result

        raw_result = await skill_tool.execute({"skill": "test-skill"})
        result = normalize_tool_result(raw_result)
        assert result.status == "success"

        # After skill trigger: mat_sg tools should be injected
        assert "mat_sg_build_bulk" in registry

        from matmaster.tools.lazy_mcp import LazyMCPTool

        lazy = registry._tools["mat_sg_build_bulk"]
        assert isinstance(lazy, LazyMCPTool)

    async def test_sync_tools_get_shorter_timeout(self, tmp_path):
        """Sync tools listed in calculation_executors get 30s timeout."""
        import yaml

        skills_root = _make_skill_dir(tmp_path)
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(exist_ok=True)
        # Cache includes both a sync tool and a non-sync tool
        schemas = [
            {"name": "build_bulk", "description": "Build bulk", "input_schema": {}},
            {"name": "run_md", "description": "Run MD simulation", "input_schema": {}},
        ]
        (cache_dir / "mat_sg.json").write_text(json.dumps(schemas))

        # MCP runtime config with calculation_executors declaring sync_tools
        (tmp_path / "mcp.yaml").write_text(
            yaml.dump(
                {
                    "calculation_preflight": "calculation",
                    "calculation_servers": ["mat_sg"],
                    "calculation_executors": {
                        "mat_sg": {
                            "executor": None,
                            "sync_tools": ["build_bulk"],
                        },
                    },
                }
            )
        )
        (tmp_path / "mcp_config.json").write_text('{"mcpServers": {}}')

        cfg = ExpConfig.model_validate(
            {
                "name": "test",
                "skills": {
                    "enabled": True,
                    "skills_root": str(skills_root),
                    "cache_dir": str(cache_dir),
                    "config_dir": str(tmp_path),
                    "mcp_config_file": "mcp_config.json",
                    "mcp_runtime_file": "mcp.yaml",
                },
            }
        )
        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock(spec=PlaygroundContext)
        ctx.session = MagicMock()
        ctx.execution_workdir = str(tmp_path)

        exp._init_skill_tools(ctx, registry)

        # Trigger skill hit to inject MCP tools
        skill_tool = registry._tools["Skill"]

        from matmaster.tools.tool_result import normalize_tool_result

        raw_result = await skill_tool.execute({"skill": "test-skill"})
        result = normalize_tool_result(raw_result)
        assert result.status == "success"

        from matmaster.tools.lazy_mcp import LazyMCPTool

        # build_bulk is a sync tool -> should get 30s timeout
        sync_tool = registry._tools["mat_sg_build_bulk"]
        assert isinstance(sync_tool, LazyMCPTool)
        assert sync_tool._timeout == 30.0

        # run_md is NOT a sync tool -> should get default 120s timeout
        async_tool = registry._tools["mat_sg_run_md"]
        assert isinstance(async_tool, LazyMCPTool)
        assert async_tool._timeout == 120.0

    def test_passes_execution_workdir_to_lazy_mcp_connector(self, tmp_path):
        """Connector should receive ctx.execution_workdir for path adaptor uploads."""
        skills_root = _make_skill_dir(tmp_path)
        cache_dir = _make_cache(tmp_path)
        _make_mcp_yaml(tmp_path)
        (tmp_path / "mcp_config.json").write_text('{"mcpServers": {}}')

        cfg = ExpConfig.model_validate(
            {
                "name": "test",
                "skills": {
                    "enabled": True,
                    "skills_root": str(skills_root),
                    "cache_dir": str(cache_dir),
                    "config_dir": str(tmp_path),
                    "mcp_config_file": "mcp_config.json",
                    "mcp_runtime_file": "mcp.yaml",
                },
            }
        )
        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock(spec=PlaygroundContext)
        ctx.session = MagicMock()
        ctx.execution_workdir = "/workspace/session-1"

        with patch("matmaster.tools.lazy_mcp.LazyMCPConnector") as mock_connector:
            exp._init_skill_tools(ctx, registry)

        mock_connector.assert_called_once_with(
            mcp_server_config={},
            mcp_config={
                "calculation_preflight": "calculation",
                "calculation_servers": ["mat_sg"],
            },
            session=ctx.session,
            workspace_path=ctx.execution_workdir,
        )

    async def test_session_local_user_skill_root_uses_existing_registry(self, tmp_path):
        skills_root = _make_skill_dir(tmp_path)
        user_skill_root = tmp_path / "user-skills"
        user_skill_dir = user_skill_root / "user-skill"
        user_skill_dir.mkdir(parents=True)
        (user_skill_dir / "SKILL.md").write_text(
            "---\nname: user-skill\ndescription: User skill\n---\n"
            "User body with ${SKILL_DIR}\n",
            encoding="utf-8",
        )
        cache_dir = _make_cache(tmp_path)
        _make_mcp_yaml(tmp_path)
        (tmp_path / "mcp_config.json").write_text('{"mcpServers": {}}')

        cfg = ExpConfig.model_validate(
            {
                "name": "test",
                "skills": {
                    "enabled": True,
                    "skills_root": str(skills_root),
                    "cache_dir": str(cache_dir),
                    "config_dir": str(tmp_path),
                    "mcp_config_file": "mcp_config.json",
                    "mcp_runtime_file": "mcp.yaml",
                },
            }
        )
        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock(spec=PlaygroundContext)
        ctx.session = MagicMock()
        ctx.session.local_user_skills_root = str(user_skill_root)
        ctx.session.remote_project_root = None
        ctx.execution_workdir = str(tmp_path)

        exp._init_skill_tools(ctx, registry)

        skill_tool = registry._tools["Skill"]
        result = await skill_tool.execute({"skill": "user-skill"})

        expected_dir = str(user_skill_dir.resolve())
        assert f"Base directory for this skill: {expected_dir}" in result
        assert f"User body with {expected_dir}" in result

    async def test_session_remote_user_skill_root_uses_ssh_registry(self, tmp_path):
        skills_root = _make_skill_dir(tmp_path)
        remote_root = "/remote/user/skills"
        session = FakeRemoteSkillSession(
            remote_root,
            {
                f"{remote_root}/remote-skill/SKILL.md": (
                    "---\n"
                    "name: remote-skill\n"
                    "description: Remote user skill\n"
                    "---\n"
                    "Remote body with ${SKILL_DIR}\n"
                ),
            },
        )
        cache_dir = _make_cache(tmp_path)
        _make_mcp_yaml(tmp_path)
        (tmp_path / "mcp_config.json").write_text('{"mcpServers": {}}')

        cfg = ExpConfig.model_validate(
            {
                "name": "test",
                "skills": {
                    "enabled": True,
                    "skills_root": str(skills_root),
                    "cache_dir": str(cache_dir),
                    "config_dir": str(tmp_path),
                    "mcp_config_file": "mcp_config.json",
                    "mcp_runtime_file": "mcp.yaml",
                },
            }
        )
        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock(spec=PlaygroundContext)
        ctx.session = session
        ctx.execution_workdir = str(tmp_path)

        exp._init_skill_tools(ctx, registry)

        skill_tool = registry._tools["Skill"]
        result = await skill_tool.execute({"skill": "remote-skill"})

        expected_dir = f"{remote_root}/remote-skill"
        assert f"Base directory for this skill: {expected_dir}" in result
        assert f"Remote body with {expected_dir}" in result

    async def test_local_skill_settings_disable_registered_skill(self, tmp_path):
        skills_root = _make_skill_dir(tmp_path)
        (skills_root / ".settings.json").write_text(
            json.dumps({"disabled": ["test-skill"]}),
            encoding="utf-8",
        )
        cache_dir = _make_cache(tmp_path)
        _make_mcp_yaml(tmp_path)
        (tmp_path / "mcp_config.json").write_text('{"mcpServers": {}}')

        cfg = ExpConfig.model_validate(
            {
                "name": "test",
                "skills": {
                    "enabled": True,
                    "skills_root": str(skills_root),
                    "cache_dir": str(cache_dir),
                    "config_dir": str(tmp_path),
                    "mcp_config_file": "mcp_config.json",
                    "mcp_runtime_file": "mcp.yaml",
                },
            }
        )
        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock(spec=PlaygroundContext)
        ctx.session = MagicMock()
        ctx.session.local_user_skills_root = None
        ctx.execution_workdir = str(tmp_path)

        exp._init_skill_tools(ctx, registry)

        skill_tool = registry._tools["Skill"]
        result = await skill_tool.execute({"skill": "test-skill"})

        assert result == "Error: Skill 'test-skill' not found"
