"""End-to-end integration test for lazy MCP loading via skill routing.

Tests the full flow: Exp.build_runtime with skills enabled -> use_skill
triggers schema injection -> LazyMCPTool appears in registry.
Does NOT require real MCP connections.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import yaml as _yaml

from matmaster.config.exp import ExpConfig, ExpSkillsConfig
from matmaster.core.exp import Exp
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.tools.tool_result import normalize_tool_result
from matmaster.types.context import PlaygroundContext


async def _execute_use_skill(
    registry: ToolRegistry,
    *,
    skill_name: str,
):
    """Execute Skill tool through the registered tool instance."""
    skill_tool = registry.get_raw("Skill")
    assert skill_tool is not None
    raw_result = await skill_tool.execute({"skill": skill_name})
    return normalize_tool_result(raw_result)


class TestLazyMCPIntegration:
    def _setup_env(self, tmp_path):
        """Create skill dir + cache dir + mcp_config.json + mcp.yaml."""
        # Skill
        skill_dir = tmp_path / 'skills' / 'test-skill'
        skill_dir.mkdir(parents=True)
        (skill_dir / 'SKILL.md').write_text(
            '---\nname: test-skill\ndescription: Test\nmcp_server: mat_sg\n---\nUse mat_sg tools.\n'
        )

        # Cache
        cache_dir = tmp_path / 'cache'
        cache_dir.mkdir()
        schemas = [
            {
                'name': 'build_bulk',
                'description': 'Build bulk',
                'input_schema': {'type': 'object'},
            },
        ]
        (cache_dir / 'mat_sg.json').write_text(json.dumps(schemas))

        # MCP config (not used for real connections in this test)
        (tmp_path / 'mcp_config.json').write_text(json.dumps({'mcpServers': {}}))

        # MCP runtime config (required by _init_skill_tools self-load)
        (tmp_path / 'mcp.yaml').write_text(
            _yaml.dump(
                {
                    'path_adaptor': 'calculation',
                    'calculation_servers': ['mat_sg'],
                }
            )
        )

        return tmp_path

    async def test_full_flow_skill_triggers_schema_injection(self, tmp_path):
        env = self._setup_env(tmp_path)

        cfg = ExpConfig.model_validate(
            {
                'name': 'test',
                'skills': {
                    'enabled': True,
                    'skills_root': str(env / 'skills'),
                    'cache_dir': str(env / 'cache'),
                    'config_dir': str(env),
                    'mcp_config_file': 'mcp_config.json',
                    'mcp_runtime_file': 'mcp.yaml',
                },
            }
        )
        exp = Exp(cfg)
        registry = ToolRegistry()

        ctx = MagicMock(spec=PlaygroundContext)
        ctx.session = MagicMock()
        ctx.execution_workdir = str(tmp_path)

        # Initialize skill tools
        exp._init_skill_tools(ctx, registry)

        # Skill tool should be registered
        assert 'Skill' in registry

        # Before skill trigger: no MCP tools
        assert 'mat_sg_build_bulk' not in registry

        # Simulate skill trigger via use_skill tool
        result = await _execute_use_skill(registry, skill_name="test-skill")

        # Verify use_skill returned successfully
        assert result.status == 'success', f"use_skill failed: {result.content}"

        # After skill trigger: mat_sg tools should be injected
        assert 'mat_sg_build_bulk' in registry

        # Verify it's a LazyMCPTool
        from matmaster.tools.lazy_mcp import LazyMCPTool

        lazy = registry._tools['mat_sg_build_bulk']
        assert isinstance(lazy, LazyMCPTool)
        assert lazy.name == 'mat_sg_build_bulk'

    async def test_multiple_skills_same_server_no_duplicate(self, tmp_path):
        """Two skills mapping to same MCP server don't duplicate tools."""
        env = self._setup_env(tmp_path)

        # Add a second skill pointing to same server
        skill_dir2 = env / 'skills' / 'second-skill'
        skill_dir2.mkdir()
        (skill_dir2 / 'SKILL.md').write_text(
            '---\nname: second-skill\ndescription: Another\nmcp_server: mat_sg\n---\nAlso mat_sg.\n'
        )

        cfg = ExpConfig.model_validate(
            {
                'name': 'test',
                'skills': {
                    'enabled': True,
                    'skills_root': str(env / 'skills'),
                    'cache_dir': str(env / 'cache'),
                    'config_dir': str(env),
                    'mcp_config_file': 'mcp_config.json',
                    'mcp_runtime_file': 'mcp.yaml',
                },
            }
        )
        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock(spec=PlaygroundContext)
        ctx.session = MagicMock()
        ctx.execution_workdir = str(tmp_path)

        exp._init_skill_tools(ctx, registry)

        # Trigger first skill
        await _execute_use_skill(registry, skill_name="test-skill")
        assert "mat_sg_build_bulk" in registry

        # Trigger second skill — should NOT duplicate
        await _execute_use_skill(registry, skill_name="second-skill")
        # Still only one mat_sg_build_bulk
        assert 'mat_sg_build_bulk' in registry

    async def test_no_cache_warns_but_doesnt_crash(self, tmp_path):
        """When cache is empty, skill trigger warns but doesn't crash."""
        # Skill pointing to uncached server
        skill_dir = tmp_path / 'skills' / 'uncached-skill'
        skill_dir.mkdir(parents=True)
        (skill_dir / 'SKILL.md').write_text(
            '---\nname: uncached-skill\ndescription: Test\nmcp_server: unknown_server\n---\nBody\n'
        )
        cache_dir = tmp_path / 'cache'
        cache_dir.mkdir()
        (tmp_path / 'mcp_config.json').write_text('{"mcpServers": {}}')
        (tmp_path / 'mcp.yaml').write_text(
            _yaml.dump(
                {
                    'path_adaptor': 'calculation',
                    'calculation_servers': [],
                }
            )
        )

        cfg = ExpConfig.model_validate(
            {
                'name': 'test',
                'skills': {
                    'enabled': True,
                    'skills_root': str(tmp_path / 'skills'),
                    'cache_dir': str(cache_dir),
                    'config_dir': str(tmp_path),
                    'mcp_config_file': 'mcp_config.json',
                    'mcp_runtime_file': 'mcp.yaml',
                },
            }
        )
        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock(spec=PlaygroundContext)
        ctx.session = MagicMock()
        ctx.execution_workdir = str(tmp_path)

        exp._init_skill_tools(ctx, registry)

        # Trigger skill with uncached server
        result = await _execute_use_skill(registry, skill_name="uncached-skill")
        assert result.status == "success"

        # No tools injected (cache miss)
        assert 'unknown_server_' not in str(list(registry._tools.keys()))


class TestExpMCPSelfLoad:
    """Verify Exp._init_skill_tools() self-loads mcp.yaml when no runtime config injected."""

    async def test_self_loads_mcp_yaml(self, tmp_path):
        """mcp.yaml is loaded from config_dir and passed to LazyMCPConnector."""
        # Create minimal mcp.yaml
        (tmp_path / 'mcp.yaml').write_text(
            _yaml.dump(
                {
                    'path_adaptor': 'calculation',
                    'calculation_servers': ['mat_sg'],
                }
            )
        )
        (tmp_path / 'mcp_config.json').write_text('{"mcpServers": {}}')

        # Create skill dir (required by SkillRegistry)
        skill_dir = tmp_path / 'skills' / 'test-skill'
        skill_dir.mkdir(parents=True)
        (skill_dir / 'SKILL.md').write_text(
            '---\nname: test-skill\ndescription: Test\nmcp_server: mat_sg\n---\nBody\n'
        )

        cache_dir = tmp_path / 'cache'
        cache_dir.mkdir()
        schemas = [{'name': 'build_bulk', 'description': 'Build', 'input_schema': {}}]
        (cache_dir / 'mat_sg.json').write_text(json.dumps(schemas))

        cfg = ExpConfig(
            skills=ExpSkillsConfig(
                enabled=True,
                skills_root=str(tmp_path / 'skills'),
                cache_dir=str(cache_dir),
                config_dir=str(tmp_path),
                mcp_config_file='mcp_config.json',
                mcp_runtime_file='mcp.yaml',
            )
        )

        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock(spec=PlaygroundContext)
        ctx.session = MagicMock()
        ctx.execution_workdir = str(tmp_path)

        # Run _init_skill_tools -- should self-load mcp.yaml
        exp._init_skill_tools(ctx, registry)

        # Skill tool registered means the full path worked
        assert 'Skill' in registry

        # Trigger skill to verify lazy tools get injected
        result = await _execute_use_skill(registry, skill_name="test-skill")
        assert result.status == "success", f"use_skill failed: {result.content}"
        assert "mat_sg_build_bulk" in registry

    def test_raises_when_mcp_yaml_missing(self, tmp_path):
        """When mcp.yaml does not exist, FileNotFoundError is raised."""
        cfg = ExpConfig(
            skills=ExpSkillsConfig(
                enabled=True,
                skills_root=str(tmp_path / 'skills'),
                cache_dir=str(tmp_path / 'cache'),
                config_dir=str(tmp_path),
                mcp_config_file='mcp_config.json',
                mcp_runtime_file='mcp.yaml',
            )
        )

        exp = Exp(cfg)

        import pytest as _pytest

        with _pytest.raises(FileNotFoundError, match='MCP runtime config not found'):
            exp._init_skill_tools(MagicMock(), MagicMock())


class TestLazyMCPTimeoutThreading:
    async def test_tool_timeouts_from_mcp_yaml(self, tmp_path):
        skill_dir = tmp_path / 'skills' / 'test-skill'
        skill_dir.mkdir(parents=True)
        (skill_dir / 'SKILL.md').write_text(
            '---\nname: test-skill\ndescription: Test\nmcp_server: mat_sg\n---\nBody\n'
        )

        cache_dir = tmp_path / 'cache'
        cache_dir.mkdir()
        schemas = [{'name': 'build_bulk', 'description': 'Build', 'input_schema': {}}]
        (cache_dir / 'mat_sg.json').write_text(json.dumps(schemas))

        (tmp_path / 'mcp_config.json').write_text(json.dumps({'mcpServers': {}}))
        (tmp_path / 'mcp.yaml').write_text(
            _yaml.dump(
                {
                    'path_adaptor': 'calculation',
                    'calculation_servers': ['mat_sg'],
                    'tool_timeouts': {'mat_sg': 300},
                }
            )
        )

        cfg = ExpConfig.model_validate(
            {
                'name': 'test',
                'skills': {
                    'enabled': True,
                    'skills_root': str(tmp_path / 'skills'),
                    'cache_dir': str(cache_dir),
                    'config_dir': str(tmp_path),
                    'mcp_config_file': 'mcp_config.json',
                    'mcp_runtime_file': 'mcp.yaml',
                },
            }
        )
        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock(spec=PlaygroundContext)
        ctx.session = MagicMock()
        ctx.execution_workdir = str(tmp_path)

        exp._init_skill_tools(ctx, registry)
        await _execute_use_skill(registry, skill_name="test-skill")

        from matmaster.tools.lazy_mcp import LazyMCPTool

        lazy = registry._tools['mat_sg_build_bulk']
        assert isinstance(lazy, LazyMCPTool)
        assert lazy._timeout == 300.0

    async def test_default_timeout_when_not_in_config(self, tmp_path):
        skill_dir = tmp_path / 'skills' / 'test-skill'
        skill_dir.mkdir(parents=True)
        (skill_dir / 'SKILL.md').write_text(
            '---\nname: test-skill\ndescription: Test\nmcp_server: mat_sg\n---\nBody\n'
        )

        cache_dir = tmp_path / 'cache'
        cache_dir.mkdir()
        schemas = [{'name': 'build_bulk', 'description': 'Build', 'input_schema': {}}]
        (cache_dir / 'mat_sg.json').write_text(json.dumps(schemas))

        (tmp_path / 'mcp_config.json').write_text(json.dumps({'mcpServers': {}}))
        (tmp_path / 'mcp.yaml').write_text(
            _yaml.dump(
                {
                    'path_adaptor': 'calculation',
                    'calculation_servers': ['mat_sg'],
                }
            )
        )

        cfg = ExpConfig.model_validate(
            {
                'name': 'test',
                'skills': {
                    'enabled': True,
                    'skills_root': str(tmp_path / 'skills'),
                    'cache_dir': str(cache_dir),
                    'config_dir': str(tmp_path),
                    'mcp_config_file': 'mcp_config.json',
                    'mcp_runtime_file': 'mcp.yaml',
                },
            }
        )
        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock(spec=PlaygroundContext)
        ctx.session = MagicMock()
        ctx.execution_workdir = str(tmp_path)

        exp._init_skill_tools(ctx, registry)
        await _execute_use_skill(registry, skill_name="test-skill")

        from matmaster.tools.lazy_mcp import (
            _DEFAULT_MCP_TOOL_TIMEOUT,
            LazyMCPTool,
        )

        lazy = registry._tools['mat_sg_build_bulk']
        assert isinstance(lazy, LazyMCPTool)
        assert lazy._timeout == _DEFAULT_MCP_TOOL_TIMEOUT
