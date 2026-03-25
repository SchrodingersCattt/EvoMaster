"""Tests for Exp._init_skill_tools with lazy MCP schema injection."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from matmaster.config.exp import ExpConfig
from matmaster.core.exp import Exp
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.types.context import PlaygroundContext


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


class TestExpInitSkillTools:
    def test_skill_tools_registered_when_enabled(self, tmp_path):
        skills_root = _make_skill_dir(tmp_path)
        cache_dir = _make_cache(tmp_path)

        cfg = ExpConfig.model_validate({
            "name": "test",
            "skills": {
                "enabled": True,
                "skills_root": str(skills_root),
                "cache_dir": str(cache_dir),
                "config_dir": str(tmp_path),
                "mcp_config_file": "mcp_config.json",
            },
        })
        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock(spec=PlaygroundContext)
        ctx.session = MagicMock()

        exp._init_skill_tools(ctx, registry)

        # use_skill tool should be registered
        assert "use_skill" in registry

    def test_skill_tools_skipped_when_disabled(self, tmp_path):
        cfg = ExpConfig.model_validate({
            "name": "test",
            "skills": {"enabled": False},
        })
        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock(spec=PlaygroundContext)

        exp._init_skill_tools(ctx, registry)

        assert "use_skill" not in registry

    def test_skill_trigger_injects_lazy_tools(self, tmp_path):
        """Simulating skill hit triggers lazy MCP tool injection."""
        skills_root = _make_skill_dir(tmp_path)
        cache_dir = _make_cache(tmp_path)
        (tmp_path / "mcp_config.json").write_text('{"mcpServers": {}}')

        cfg = ExpConfig.model_validate({
            "name": "test",
            "skills": {
                "enabled": True,
                "skills_root": str(skills_root),
                "cache_dir": str(cache_dir),
                "config_dir": str(tmp_path),
                "mcp_config_file": "mcp_config.json",
            },
        })
        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock(spec=PlaygroundContext)
        ctx.session = MagicMock()

        exp._init_skill_tools(ctx, registry)

        # Before skill trigger: no MCP tools
        assert "mat_sg_build_bulk" not in registry

        # Trigger via use_skill
        result = registry.execute(
            "use_skill", {"skill_name": "test-skill", "action": "get_info"}
        )
        assert not result.startswith("Error:"), f"use_skill failed: {result}"

        # After skill trigger: mat_sg tools should be injected
        assert "mat_sg_build_bulk" in registry

        from matmaster.tools.lazy_mcp import LazyMCPTool
        lazy = registry._tools["mat_sg_build_bulk"]
        assert isinstance(lazy, LazyMCPTool)
