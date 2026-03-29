"""Unit tests for SpawnTool.

Tests cover:
- spawn_fn invocation with correct arguments
- Recursion guard (spawn_fn=None returns error)
- Input validation (missing/empty exp_name and task)
- Exception handling via BuiltinTool.execute wrapper
- ClassVar protocol satisfaction (name, description, json_schema)
- explore.toml loading and schema-layer recursion guard
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest


class TestSpawnToolExecute:
    """Tests for SpawnTool._execute behavior."""

    async def test_execute_calls_spawn_fn(self) -> None:
        """SpawnTool with mock spawn_fn calls it with (exp_name, task, stop_event)."""
        from matmaster.tools.builtin.spawn_tool import SpawnTool

        mock_spawn = AsyncMock(return_value="exploration result: found 3 files")
        tool = SpawnTool(spawn_fn=mock_spawn)

        result = await tool.execute({"exp_name": "explore", "task": "find files"})

        # 3-arg call: (exp_name, task, _stop_event=None)
        mock_spawn.assert_called_once_with("explore", "find files", None)
        assert result == "exploration result: found 3 files"

    async def test_recursion_guard_spawn_fn_none(self) -> None:
        """SpawnTool(spawn_fn=None) returns error containing 'not available'."""
        from matmaster.tools.builtin.spawn_tool import SpawnTool

        tool = SpawnTool(spawn_fn=None)

        result = await tool.execute({"exp_name": "x", "task": "y"})

        assert "not available" in result.lower()

    async def test_missing_exp_name(self) -> None:
        """execute({"task": "y"}) returns error containing 'required'."""
        from matmaster.tools.builtin.spawn_tool import SpawnTool

        mock_spawn = AsyncMock(return_value="ok")
        tool = SpawnTool(spawn_fn=mock_spawn)

        result = await tool.execute({"task": "y"})

        assert "required" in result.lower()
        mock_spawn.assert_not_called()

    async def test_missing_task(self) -> None:
        """execute({"exp_name": "x"}) returns error containing 'required'."""
        from matmaster.tools.builtin.spawn_tool import SpawnTool

        mock_spawn = AsyncMock(return_value="ok")
        tool = SpawnTool(spawn_fn=mock_spawn)

        result = await tool.execute({"exp_name": "x"})

        assert "required" in result.lower()
        mock_spawn.assert_not_called()

    async def test_empty_exp_name(self) -> None:
        """execute({"exp_name": "", "task": "y"}) returns error containing 'required'."""
        from matmaster.tools.builtin.spawn_tool import SpawnTool

        mock_spawn = AsyncMock(return_value="ok")
        tool = SpawnTool(spawn_fn=mock_spawn)

        result = await tool.execute({"exp_name": "", "task": "y"})

        assert "required" in result.lower()
        mock_spawn.assert_not_called()

    async def test_empty_task(self) -> None:
        """execute({"exp_name": "x", "task": ""}) returns error containing 'required'."""
        from matmaster.tools.builtin.spawn_tool import SpawnTool

        mock_spawn = AsyncMock(return_value="ok")
        tool = SpawnTool(spawn_fn=mock_spawn)

        result = await tool.execute({"exp_name": "x", "task": ""})

        assert "required" in result.lower()
        mock_spawn.assert_not_called()

    async def test_spawn_fn_exception_handled(self) -> None:
        """spawn_fn raises ValueError, execute returns 'Error: ...' via SpawnTool.execute()."""
        from matmaster.tools.builtin.spawn_tool import SpawnTool

        mock_spawn = AsyncMock(side_effect=ValueError("unknown exp: bad_name"))
        tool = SpawnTool(spawn_fn=mock_spawn)

        result = await tool.execute({"exp_name": "bad_name", "task": "do stuff"})

        assert result.startswith("Error:")
        assert "unknown exp: bad_name" in result


class TestSpawnToolClassVars:
    """Tests for SpawnTool class-level attributes (Tool Protocol)."""

    def test_class_vars(self) -> None:
        """SpawnTool satisfies Tool Protocol class vars."""
        from matmaster.tools.builtin.spawn_tool import SpawnTool

        assert SpawnTool.name == "spawn"
        assert isinstance(SpawnTool.description, str)
        assert len(SpawnTool.description) > 0

        schema = SpawnTool.json_schema
        assert schema["type"] == "object"
        assert "exp_name" in schema["properties"]
        assert "task" in schema["properties"]
        assert "exp_name" in schema["required"]
        assert "task" in schema["required"]


class TestExploreToml:
    """Tests for explore.toml exp definition."""

    def test_explore_toml_loads(self) -> None:
        """load_exp_config('explore') returns valid ExpConfig."""
        from matmaster.config.loader import load_exp_config

        cfg = load_exp_config("explore")

        assert cfg.name == "explore"
        assert "spawn" not in cfg.tools.builtin
        assert len(cfg.developer_instructions) > 0

    def test_explore_toml_name(self) -> None:
        """explore.toml has name='explore'."""
        from matmaster.config.loader import load_exp_config

        cfg = load_exp_config("explore")
        assert cfg.name == "explore"

    def test_explore_toml_max_turns(self) -> None:
        """explore.toml has lower max_turns than direct.toml (50 vs 200)."""
        from matmaster.config.loader import load_exp_config

        cfg = load_exp_config("explore")
        assert cfg.max_turns == 50

    def test_explore_toml_skills_disabled(self) -> None:
        """explore.toml has skills disabled."""
        from matmaster.config.loader import load_exp_config

        cfg = load_exp_config("explore")
        assert cfg.skills.enabled is False

    def test_explore_toml_no_mcp(self) -> None:
        """explore.toml has no MCP access."""
        from matmaster.config.loader import load_exp_config

        cfg = load_exp_config("explore")
        assert cfg.tools.mcp == ""
