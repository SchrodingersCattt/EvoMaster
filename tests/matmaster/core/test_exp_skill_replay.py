"""Tests for Exp on_skill_hit external record callback and active-server replay."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import yaml as _yaml

from matmaster.config.exp import ExpConfig
from matmaster.core.exp import Exp
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.tools.tool_result import normalize_tool_result
from matmaster.types.context import PlaygroundContext


def _setup_skill_env(tmp_path):
    skill_dir = tmp_path / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: Test\nmcp_server: mat_sg\n---\nUse mat_sg.\n"
    )
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "mat_sg.json").write_text(
        json.dumps(
            [{"name": "build_bulk", "description": "Build", "input_schema": {}}]
        )
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
async def test_on_skill_hit_invokes_record_active_mcp_server(tmp_path):
    """on_skill_hit must notify the run_meta callback with the activated server."""
    env = _setup_skill_env(tmp_path)
    cfg = _build_cfg(env)
    exp = Exp(cfg)
    registry = ToolRegistry()

    recorded: list[str] = []
    ctx = MagicMock(spec=PlaygroundContext)
    ctx.session = MagicMock()
    ctx.execution_workdir = str(tmp_path)
    ctx.run_meta = {"record_active_mcp_server": recorded.append}

    exp._init_skill_tools(ctx, registry)

    skill_tool = registry.get_raw("Skill")
    raw_result = await skill_tool.execute({"skill": "test-skill"})
    result = normalize_tool_result(raw_result)
    assert result.status == "success", result.content

    assert recorded == ["mat_sg"]
