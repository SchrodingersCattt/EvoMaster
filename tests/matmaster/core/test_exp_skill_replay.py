"""Tests for Exp skill-driven active-server replay."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import yaml as _yaml

from matmaster.config.exp import ExpConfig
from matmaster.core.exp import Exp
from matmaster.core.playground import PlaygroundContext
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.tools.tool_result import normalize_tool_result
from matmaster.types.run_metadata import RunMetadata


def _setup_skill_env(tmp_path):
    skill_dir = tmp_path / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: Test\nmcp_server: mat_sg\n---\nUse mat_sg.\n"
    )
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "mat_sg.json").write_text(
        json.dumps([{"name": "build_bulk", "description": "Build", "input_schema": {}}])
    )
    (tmp_path / "mcp_config.json").write_text(json.dumps({"mcpServers": {}}))
    (tmp_path / "mcp.yaml").write_text(
        _yaml.dump(
            {
                "calculation_preflight": "calculation",
                "calculation_servers": ["mat_sg"],
            }
        )
    )
    return tmp_path


def _build_cfg(env):
    return ExpConfig.model_validate(
        {
            "name": "test",
            "skills": {
                "enabled": True,
                "skills_root": str(env / "skills"),
                "cache_dir": str(env / "cache"),
                "config_dir": str(env),
                "mcp_config_file": "mcp_config.json",
                "mcp_runtime_file": "mcp.yaml",
            },
        }
    )


@pytest.mark.asyncio
async def test_on_skill_hit_does_not_require_metadata_record_callback(tmp_path):
    """Skill execution still injects MCP tools without a metadata record callback."""
    env = _setup_skill_env(tmp_path)
    cfg = _build_cfg(env)
    exp = Exp(cfg)
    registry = ToolRegistry()

    ctx = MagicMock(spec=PlaygroundContext)
    ctx.session = MagicMock()
    ctx.execution_workdir = str(tmp_path)
    ctx.metadata = RunMetadata()

    exp._init_skill_tools(ctx, registry)

    skill_tool = registry.get_raw("Skill")
    raw_result = await skill_tool.execute({"skill": "test-skill"})
    result = normalize_tool_result(raw_result)
    assert result.status == "success", result.content

    assert "mat_sg_build_bulk" in registry


@pytest.mark.asyncio
async def test_init_skill_tools_replays_active_skills_into_registry(tmp_path):
    """active_skills in metadata must activate skill-declared MCP servers."""
    env = _setup_skill_env(tmp_path)
    cfg = _build_cfg(env)
    exp = Exp(cfg)
    registry = ToolRegistry()

    ctx = MagicMock(spec=PlaygroundContext)
    ctx.session = MagicMock()
    ctx.execution_workdir = str(tmp_path)
    ctx.metadata = RunMetadata(active_skills=frozenset({"test-skill"}))

    # No use_skill call -- replay must inject the lazy tool by itself.
    exp._init_skill_tools(ctx, registry)

    assert "mat_sg_build_bulk" in registry

    from matmaster.tools.lazy_mcp import LazyMCPTool

    tool = registry.get_raw("mat_sg_build_bulk")
    assert isinstance(tool, LazyMCPTool)


@pytest.mark.asyncio
async def test_replay_is_idempotent_with_use_skill(tmp_path):
    """Replay + a fresh use_skill call must not duplicate the tool."""
    env = _setup_skill_env(tmp_path)
    cfg = _build_cfg(env)
    exp = Exp(cfg)
    registry = ToolRegistry()

    ctx = MagicMock(spec=PlaygroundContext)
    ctx.session = MagicMock()
    ctx.execution_workdir = str(tmp_path)
    ctx.metadata = RunMetadata(active_skills=frozenset({"test-skill"}))

    exp._init_skill_tools(ctx, registry)
    assert "mat_sg_build_bulk" in registry

    tool_before = registry.get_raw("mat_sg_build_bulk")

    skill_tool = registry.get_raw("Skill")
    raw_result = await skill_tool.execute({"skill": "test-skill"})
    result = normalize_tool_result(raw_result)
    assert result.status == "success"

    tool_after = registry.get_raw("mat_sg_build_bulk")
    assert tool_after is tool_before


@pytest.mark.asyncio
async def test_old_active_mcp_servers_field_is_ignored(tmp_path):
    """The old active_mcp_servers field must not activate tools."""
    env = _setup_skill_env(tmp_path)
    cfg = _build_cfg(env)
    exp = Exp(cfg)
    registry = ToolRegistry()

    ctx = MagicMock(spec=PlaygroundContext)
    ctx.session = MagicMock()
    ctx.execution_workdir = str(tmp_path)
    ctx.metadata = RunMetadata()

    exp._init_skill_tools(ctx, registry)

    assert "Skill" in registry
    assert "mat_sg_build_bulk" not in registry


@pytest.mark.asyncio
async def test_replay_silently_skips_missing_skill(tmp_path):
    """A missing skill in active set must not crash or activate tools."""
    env = _setup_skill_env(tmp_path)
    cfg = _build_cfg(env)
    exp = Exp(cfg)
    registry = ToolRegistry()

    ctx = MagicMock(spec=PlaygroundContext)
    ctx.session = MagicMock()
    ctx.execution_workdir = str(tmp_path)
    ctx.metadata = RunMetadata(active_skills=frozenset({"missing-skill"}))

    exp._init_skill_tools(ctx, registry)

    assert "Skill" in registry
    assert "mat_sg_build_bulk" not in registry
